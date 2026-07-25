import logging
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("aeroguard")

DEV_KEY_PLACEHOLDER = "change-me-to-a-long-random-value"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AEROGUARD_", env_file=".env", extra="ignore"
    )

    # Single-operator key (one-click launch, local development).
    api_key: str = ""
    operator_name: str = "console"
    # Multi-operator deployment: "twr-1:KEY1,gnd-2:KEY2". Each key maps to a
    # named operator so the audit trail attributes actions to a person
    # rather than to a shared console. Takes precedence over api_key.
    api_keys: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: str = "data/aeroguard.db"
    # Keyed audit chain. Without a key the chain is a plain SHA-256 hash
    # chain, which anyone with write access to the database can recompute.
    # Left empty a key is generated once and persisted next to the database;
    # in production supply it from the platform secret store instead.
    audit_key: str = ""
    audit_key_path: str = ""
    # Append-only chain-head anchors. Point this at a different volume (or a
    # WORM/remote mount) so a full database rewrite is still detectable.
    audit_anchor_path: str = ""
    # Comma-separated CIDRs of reverse proxies allowed to set
    # X-Forwarded-For. Empty (default) = never trust the header.
    trusted_proxies: str = ""
    # Live pose streaming dominates this budget: the HMI captures at 70 ms
    # (~857 req/min) and classifies at 400 ms (~150 req/min), so one
    # operator costs ~1000 req/min. Sized for two operators sharing an
    # address (NAT, or two tabs on one workstation) with headroom, while
    # still bounding abuse on the LAN.
    rate_limit_per_minute: int = 2400
    log_level: str = "INFO"
    # Structured logs are the deployment default; the plain formatter stays
    # available because it is far easier to read at a terminal.
    json_logs: bool = True
    # Days of audit history to retain. Startup verification reads the whole
    # table, so an unbounded log makes boot time track total history. Live
    # classification only writes on a change of signal (see api/routes.py),
    # which keeps the volume operator-paced. Set 0 to keep everything.
    audit_retention_days: int = 90
    max_alerts_in_memory: int = 500
    # Consecutive classification windows an alerting marshalling signal
    # must hold before it fires, and how many it must then be absent for
    # before it can fire again. At the HMI's ~1.6s window cadence this
    # trades ~1.6s of latency for suppressing single-window false
    # positives and repeat takeovers. See fusion/gating.py.
    signal_confirmations: int = 2
    signal_release_windows: int = 3

    def resolve_keys(self) -> dict[str, str]:
        """Return the operator-identity → API-key map for this process."""
        if self.api_keys.strip():
            keys: dict[str, str] = {}
            for entry in self.api_keys.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                name, sep, key = entry.partition(":")
                if not sep or not name.strip() or not key.strip():
                    raise ValueError(
                        "AEROGUARD_API_KEYS entries must be 'operator:key'"
                    )
                keys[name.strip()] = key.strip()
            if not keys:
                raise ValueError("AEROGUARD_API_KEYS is set but empty")
            # Keep single-key consumers (HMI bootstrap, tests) working.
            self.api_key = next(iter(keys.values()))
            return keys
        return {self.operator_name: self.resolve_api_key()}

    def resolve_api_key(self) -> str:
        if not self.api_key or self.api_key == DEV_KEY_PLACEHOLDER:
            generated = secrets.token_urlsafe(24)
            logger.warning(
                "AEROGUARD_API_KEY not set — generated ephemeral dev key: %s",
                generated,
            )
            self.api_key = generated
        return self.api_key


settings = Settings()
