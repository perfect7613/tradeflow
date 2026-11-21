"""All configuration comes from environment variables (or backend/.env)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Sarvam -----------------------------------------------------------
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    model_reasoning: str = "glm5.3"  # Tier 2
    model_reasoning_backup: str = "deepseekv4.1-flash-1017"  # Kimi K3.1 Pro is not enabled on our key
    model_extract: str = "deepseekv4.1-flash-1017"
    model_conversation: str = "sarvam-105b-conversations"
    tts_model: str = "bulbul:v3"
    tts_speaker: str = ""  # empty = model default voice
    stt_model: str = "saaras:v3"

    # Optional: Sarvam Voice Agents (real outbound phone calls)
    sarvam_agents_api_key: str = ""
    sarvam_org_id: str = ""
    sarvam_workspace_id: str = ""
    sarvam_app_id: str = ""
    sarvam_app_version: int = 1
    sarvam_connection_id: str = ""
    sarvam_agent_phone: str = ""

    # --- Cashfree (sandbox). Leave blank to run on the simulated rails. ---
    cashfree_env: str = "sandbox"  # sandbox | production
    cashfree_payouts_client_id: str = ""
    cashfree_payouts_client_secret: str = ""
    cashfree_payouts_public_key_path: str = ""  # 2FA public key (.pem); blank = IP whitelist
    cashfree_verify_client_id: str = ""
    cashfree_verify_client_secret: str = ""
    cashfree_verify_public_key_path: str = ""
    cashfree_pg_client_id: str = ""
    cashfree_pg_client_secret: str = ""
    cashfree_fundsource_id: str = ""

    # --- App --------------------------------------------------------------
    public_url: str = "http://localhost:8000"  # ngrok URL in a demo
    owner_language: str = "ta-IN"
    auto_pay_limit: int = 5_000  # rupees; above this the owner always decides
    pin_limit: int = 1_00_000  # rupees; above this the owner also gives a PIN
    owner_pin: str = "2580"
    approval_window_s: float = 10.0
    call_hours: tuple[int, int] = (8, 19)  # IST, inclusive start, exclusive end
    enforce_call_hours: bool = False  # demo runs at any hour; flip on for real use
    poll_fallback_s: float = 8.0  # poll payout status if no webhook arrives in time
    web_dir: str = str(Path(__file__).resolve().parents[3] / "web" / "out")

    @property
    def cashfree_live(self) -> bool:
        return bool(self.cashfree_payouts_client_id and self.cashfree_payouts_client_secret)

    @property
    def sarvam_phone_calls(self) -> bool:
        return all(
            [
                self.sarvam_agents_api_key,
                self.sarvam_org_id,
                self.sarvam_workspace_id,
                self.sarvam_app_id,
                self.sarvam_connection_id,
                self.sarvam_agent_phone,
            ]
        )


@lru_cache
def settings() -> Settings:
    return Settings()
