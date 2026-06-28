from pydantic import BaseModel, ConfigDict


class SopsRule(BaseModel):
    """One SOPS ``creation_rule``: which age recipients may encrypt a given
    secret file. ``path_regex`` is an anchored regex matching exactly one
    secret source; ``recipients`` is the operator keys plus the age recipients
    of every host that references that secret, deduped and ordered. Built in
    normalization so the renderer only formats (renderer contract). See T-080.
    """

    model_config = ConfigDict(frozen=True)

    path_regex: str
    recipients: tuple[str, ...]


class SopsConfigModel(BaseModel):
    """The generated ``.sops.yaml``: one rule per distinct secret source, in
    source order. There is deliberately no catch-all rule -- every secret is
    modeled (it has a SecretSource), so each gets an exact, declarative
    recipient set derived from the secret->host map. See T-080."""

    model_config = ConfigDict(frozen=True)

    rules: tuple[SopsRule, ...]
