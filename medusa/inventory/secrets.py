from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SecretsInventory(BaseModel):
    """Fleet-wide secrets configuration. Today this is just the operator age
    recipients: the human/admin public keys that must be able to author and
    re-encrypt every secret in addition to the host that consumes it.

    These are *public* age recipients (``age1...``), not key material. Medusa
    models recipients/references only -- never plaintext (Secrets ADR, T-080).
    """

    model_config = ConfigDict(extra="forbid")

    # Operator recipients added to every generated creation_rule. Without at
    # least one, losing a host key would make that host's secrets unrecoverable
    # and unauthorable (T-080 open question: host-key rotation = re-encrypt).
    operators: list[str] = Field(default_factory=list)

    @field_validator("operators")
    @classmethod
    def normalize_operators(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("operator age recipients cannot be empty")
        if any(not item.startswith("age1") for item in normalized):
            raise ValueError("operator age recipients must be age1 public keys")
        return normalized

    @model_validator(mode="after")
    def validate_unique_operators(self) -> Self:
        if len(self.operators) != len(set(self.operators)):
            raise ValueError("operator age recipients must be unique")
        return self


def parse_secrets_inventory(data: dict | None) -> SecretsInventory:
    if data is None:
        return SecretsInventory()
    return SecretsInventory.model_validate(data)
