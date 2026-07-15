"""
PMS5003 particulate matter sensor driver.

The Plantower PMS5003 is a laser-based particulate matter sensor that
reports PM1.0, PM2.5, and PM10 mass concentrations (µg/m³) along with
particle counts across six size bins.  It communicates over a 3.3 V
TTL serial line at 9600 baud and pushes a 32-byte data frame roughly
once per second.

WHY this matters for weather:
  Particulate matter is a critical air-quality and visibility indicator.
  PM2.5 (≤2.5 µm) penetrates deep into the lungs and is tracked by every
  major air-quality standard (EPA AQI, EU CAQI, WHO).  The particle-count
  bins give a coarse particle-size distribution, useful for distinguishing
  dust events from combustion sources (wildfire smoke skews sub-micron;
  road dust skews coarse).

Frame format (32 bytes, little-endian big-endian mix — see datasheet):
  Byte  0-1 : start marker  0x42 0x4d
  Byte  2-3 : frame length  (0x00 0x1c = 28)
  Byte  4-5 : PM1.0  (µg/m³, "standard" / atmospheric)
  Byte  6-7 : PM2.5  (µg/m³)
  Byte  8-9 : PM10   (µg/m³)
  Byte 10-11: PM1.0  (µg/m³, "factory env" — under ambient conditions)
  Byte 12-13: PM2.5  (µg/m³, factory env)
  Byte 14-15: PM10   (µg/m³, factory env)
  Byte 16-17: count of particles > 0.3 µm in 0.1 L air
  Byte 18-19: count of particles > 0.5 µm
  Byte 20-21: count of particles > 1.0 µm
  Byte 22-23: count of particles > 2.5 µm
  Byte 24-25: count of particles > 5.0 µm
  Byte 26-27: count of particles > 10  µm
  Byte 28-29: reserved
  Byte 30-31: checksum (sum of bytes 0-29)

NOTE: We report the "standard" (atmospheric) PM values from bytes 4-9,
      not the "factory env" values from bytes 10-15.  The standard
      values are calibrated to dry, sea-level conditions and are what
      regulatory indices expect.

NOTE: pyserial is an optional dependency.  Importing this module on a
      non-Pi dev machine will not fail — the serial library is wrapped
      in try/except so mock mode works anywhere.
"""

from __future__ import annotations

import logging
import struct
import time
from dataclasses import dataclass

from weather_station.core.sensor_base import SensorBase, SensorReading
from weather_station.core.mock_manager import MockManager

logger = logging.getLogger(__name__)

# ── Optional hardware library ────────────────────────────────────────────
# pyserial provides the serial.Serial class for talking to /dev/serial0.
# We guard the import so the module loads on dev machines without it.
try:
    import serial  # type: ignore[import-untyped]
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False
    logger.debug("pyserial not available — PMS5003 will require mock mode")


# ── Constants ────────────────────────────────────────────────────────────
_PMS5003_FRAME_SIZE = 32          # total bytes in a data frame
_PMS5003_START_BYTE1 = 0x42       # first start marker
_PMS5003_START_BYTE2 = 0x4D       # second start marker
_PMS5003_READ_TIMEOUT = 2.0       # seconds to wait for a complete frame
_PMS5003_SYNC_RETRIES = 10        # max bytes to scan when re-syncing


@dataclass(frozen=True)
class PMS5003Config:
    """Immutable configuration for the PMS5003 driver.

    WHY frozen: prevents accidental reconfiguration of a live sensor
    handle, which could leave the serial port in a bad state.
    """

    serial_port: str = "/dev/serial0"
    baudrate: int = 9600


class PMS5003Sensor(SensorBase):
    """Driver for the Plantower PMS5003 particulate matter sensor.

    Metrics produced (see module docstring for full frame layout):
      pm1_0_ugm3   — PM1.0 mass concentration (µg/m³)
      pm2_5_ugm3   — PM2.5 mass concentration (µg/m³)
      pm10_ugm3    — PM10  mass concentration (µg/m³)
      pm_n_0_3um   — particle count > 0.3 µm per 0.1 L
      pm_n_0_5um   — particle count > 0.5 µm per 0.1 L
      pm_n_1_0um   — particle count > 1.0 µm per 0.1 L
      pm_n_2_5um   — particle count > 2.5 µm per 0.1 L
      pm_n_5_0um   — particle count > 5.0 µm per 0.1 L
      pm_n_10um    — particle count > 10  µm per 0.1 L
    """

    name = "pms5003"
    bus_type = "serial"
    description = "PMS5003 particulate matter sensor (PM1.0/2.5/10 + particle counts)"
    metrics = [
        "pm1_0_ugm3",
        "pm2_5_ugm3",
        "pm10_ugm3",
        "pm_n_0_3um",
        "pm_n_0_5um",
        "pm_n_1_0um",
        "pm_n_2_5um",
        "pm_n_5_0um",
        "pm_n_10um",
    ]

    def __init__(
        self,
        serial_port: str = "/dev/serial0",
        baudrate: int = 9600,
        mock_mode: bool = False,
    ) -> None:
        # Store config as a frozen dataclass for immutability and clarity
        self._config = PMS5003Config(serial_port=serial_port, baudrate=baudrate)
        # Serial handle is created lazily in _init_hardware()
        self._serial_conn = None
        # MockManager instance — shared-style with other drivers; created
        # lazily so we don't pay for it in hardware mode.
        self._mock: MockManager | None = None
        super().__init__(mock_mode=mock_mode)

    # ── Hardware lifecycle ──────────────────────────────────────────────

    def _init_hardware(self) -> bool:
        """Open the serial port.  Return False if unavailable."""
        if not _HAS_SERIAL:
            logger.error(
                "[pms5003] pyserial not installed — cannot use hardware mode"
            )
            return False

        try:
            self._serial_conn = serial.Serial(
                port=self._config.serial_port,
                baudrate=self._config.baudrate,
                timeout=_PMS5003_READ_TIMEOUT,
            )
            # Flush any partial frame left in the UART buffer so our first
            # read starts on a clean frame boundary.
            self._serial_conn.reset_input_buffer()
            logger.info(
                "[pms5003] serial port %s opened at %d baud",
                self._config.serial_port,
                self._config.baudrate,
            )
            return True
        except serial.SerialException as e:
            # Most common cause: /dev/serial0 not available (no UART enabled
            # in config.txt, or device busy).  Log and bail.
            logger.error("[pms5003] cannot open serial port: %s", e)
            self._serial_conn = None
            return False
        except Exception as e:  # pragma: no cover — defensive
            logger.error("[pms5003] unexpected init error: %s", e)
            self._serial_conn = None
            return False

    # ── Reading ─────────────────────────────────────────────────────────

    def _read_hardware(self) -> SensorReading | None:
        """Read and parse one 32-byte PMS5003 data frame.

        The sensor pushes frames continuously, but we may land mid-frame.
        We scan for the 0x42 0x4D start marker, then read the remaining 30
        bytes and validate the checksum.  On timeout or checksum failure
        we return None so the base class records a failure.
        """
        if self._serial_conn is None:
            logger.warning("[pms5003] no serial connection — call initialize() first")
            return None

        try:
            frame = self._read_frame()
            if frame is None:
                # Timeout or sync failure — let the base class count it.
                return None

            # ── Validate checksum ───────────────────────────────────────
            # Checksum is the sum of all bytes 0-29, stored big-endian in
            # bytes 30-31.
            checksum = sum(frame[:30])
            stored_checksum = (frame[30] << 8) | frame[31]
            if checksum != stored_checksum:
                logger.warning(
                    "[pms5003] checksum mismatch: calc=0x%04x stored=0x%04x",
                    checksum,
                    stored_checksum,
                )
                return None

            # ── Parse PM mass concentrations (bytes 4-9, big-endian) ────
            # Each value is a 16-bit unsigned int (big-endian per datasheet).
            pm1_0 = (frame[4] << 8) | frame[5]
            pm2_5 = (frame[6] << 8) | frame[7]
            pm10 = (frame[8] << 8) | frame[9]

            # ── Parse particle counts (bytes 16-27, big-endian) ────────
            pm_n_0_3 = (frame[16] << 8) | frame[17]
            pm_n_0_5 = (frame[18] << 8) | frame[19]
            pm_n_1_0 = (frame[20] << 8) | frame[21]
            pm_n_2_5 = (frame[22] << 8) | frame[23]
            pm_n_5_0 = (frame[24] << 8) | frame[25]
            pm_n_10 = (frame[26] << 8) | frame[27]

            return SensorReading(
                sensor_name=self.name,
                metrics={
                    "pm1_0_ugm3": float(pm1_0),
                    "pm2_5_ugm3": float(pm2_5),
                    "pm10_ugm3": float(pm10),
                    "pm_n_0_3um": float(pm_n_0_3),
                    "pm_n_0_5um": float(pm_n_0_5),
                    "pm_n_1_0um": float(pm_n_1_0),
                    "pm_n_2_5um": float(pm_n_2_5),
                    "pm_n_5_0um": float(pm_n_5_0),
                    "pm_n_10um": float(pm_n_10),
                },
                units={
                    "pm1_0_ugm3": "ugm3",
                    "pm2_5_ugm3": "ugm3",
                    "pm10_ugm3": "ugm3",
                    "pm_n_0_3um": "count/0.1L",
                    "pm_n_0_5um": "count/0.1L",
                    "pm_n_1_0um": "count/0.1L",
                    "pm_n_2_5um": "count/0.1L",
                    "pm_n_5_0um": "count/0.1L",
                    "pm_n_10um": "count/0.1L",
                },
                metadata={
                    "raw_frame": frame.hex(),
                    "frame_length": (frame[2] << 8) | frame[3],
                },
            )

        except serial.SerialException as e:
            # Port was disconnected or hardware error — clean up and bail.
            logger.error("[pms5003] serial read error: %s", e)
            self._cleanup_serial()
            return None
        except Exception as e:  # pragma: no cover — defensive
            logger.error("[pms5003] unexpected read error: %s", e)
            self._cleanup_serial()
            return None

    def _read_frame(self) -> bytes | None:
        """Read bytes until we find a valid frame start, then read the rest.

        Returns the full 32-byte frame or None on timeout / sync failure.
        We scan up to ``_PMS5003_SYNC_RETRIES`` bytes looking for the
        0x42 0x4D marker before giving up.
        """
        conn = self._serial_conn
        assert conn is not None

        # ── Phase 1: find the start marker ─────────────────────────────
        # The sensor streams frames back-to-back, so we may be anywhere
        # in the stream when we start reading.
        found_start = False
        for _ in range(_PMS5003_SYNC_RETRIES * 2):
            b = conn.read(1)
            if not b:
                # Read timed out — no data available within timeout window
                logger.debug("[pms5003] sync read timed out")
                return None
            if b[0] == _PMS5003_START_BYTE1:
                b2 = conn.read(1)
                if not b2:
                    return None
                if b2[0] == _PMS5003_START_BYTE2:
                    found_start = True
                    break

        if not found_start:
            logger.warning("[pms5003] could not find frame start marker")
            return None

        # ── Phase 2: read the remaining 30 bytes ───────────────────────
        # We already consumed 2 start bytes; need 30 more to complete 32.
        remaining = conn.read(_PMS5003_FRAME_SIZE - 2)
        if len(remaining) < _PMS5003_FRAME_SIZE - 2:
            # Partial frame — sensor stopped sending or timed out
            logger.warning(
                "[pms5003] incomplete frame: got %d of %d bytes",
                len(remaining) + 2,
                _PMS5003_FRAME_SIZE,
            )
            return None

        return bytes([_PMS5003_START_BYTE1, _PMS5003_START_BYTE2]) + remaining

    def _read_mock(self) -> SensorReading:
        """Generate plausible mock particulate data via MockManager."""
        if self._mock is None:
            self._mock = MockManager()
        m = self._mock
        return SensorReading(
            sensor_name=self.name,
            metrics={
                "pm1_0_ugm3": m.get("pm1_0_ugm3"),
                "pm2_5_ugm3": m.get("pm2_5_ugm3"),
                "pm10_ugm3": m.get("pm10_ugm3"),
                "pm_n_0_3um": m.get("pm_n_0_3um"),
                "pm_n_0_5um": m.get("pm_n_0_5um"),
                "pm_n_1_0um": m.get("pm_n_1_0um"),
                "pm_n_2_5um": m.get("pm_n_2_5um"),
                "pm_n_5_0um": m.get("pm_n_5_0um"),
                "pm_n_10um": m.get("pm_n_10um"),
            },
            units={
                "pm1_0_ugm3": "ugm3",
                "pm2_5_ugm3": "ugm3",
                "pm10_ugm3": "ugm3",
                "pm_n_0_3um": "count/0.1L",
                "pm_n_0_5um": "count/0.1L",
                "pm_n_1_0um": "count/0.1L",
                "pm_n_2_5um": "count/0.1L",
                "pm_n_5_0um": "count/0.1L",
                "pm_n_10um": "count/0.1L",
            },
            metadata={"mock": True},
        )

    # ── Cleanup ─────────────────────────────────────────────────────────

    def _cleanup_serial(self) -> None:
        """Close and null out the serial connection on error."""
        if self._serial_conn is not None:
            try:
                self._serial_conn.close()
            except Exception:  # pragma: no cover — best-effort close
                pass
            self._serial_conn = None

    def __del__(self) -> None:  # noqa: B003 — intentional cleanup
        """Ensure the serial port is released when the driver is GC'd."""
        self._cleanup_serial()