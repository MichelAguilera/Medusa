from medusa.model.services import ComposeService, GeneratedEnvFile, SecretSource

# Where a host's decrypted secret plaintext lives at runtime: tmpfs (/run), not
# the on-disk stacks dir. The host decrypts here locally with its own ssh host
# key; the controller never ships plaintext (host-side decryption, T-080). The
# Ansible decrypt unit writes ``<root>/<SecretSource.destination>`` and Compose
# references the same absolute paths, so this is the single source of truth for
# both. Mirrors NixOS's /run/secrets; namespaced to avoid colliding with it.
SECRET_RUNTIME_ROOT = "/run/medusa-secrets"


def secret_runtime_path(destination: str) -> str:
    """Absolute tmpfs path a secret's plaintext is decrypted to on the host,
    from its manifest ``destination``. Used by both the Compose references and
    (via the same root) the Ansible decrypt unit. See T-080."""
    return f"{SECRET_RUNTIME_ROOT}/{destination}"


def managed_env_files(service) -> tuple[str, ...]:
    env_files: list[str] = []
    if managed_environment(service):
        env_files.append(f"./env/{service.name}.env")
    if _managed_secret_env_settings(service):
        # Secret env file is decrypted to tmpfs at boot, not written to the
        # stacks dir; reference it by absolute /run path (T-080).
        env_files.append(
            secret_runtime_path(
                f"{_stack_path(service)}/env/{service.name}.secrets.env"
            )
        )
    return tuple(env_files)


def managed_environment(service) -> dict[str, str]:
    environment: dict[str, str] = {}
    for name, binding in sorted(service.settings.items()):
        sensitive = binding.secret is not None
        delivery = binding.delivery or "env"
        if sensitive:
            if delivery == "file_env":
                environment[binding.file_env] = f"/run/secrets/{_secret_name(name)}"
            continue

        if delivery in {"env", "file_env"}:
            environment[name] = _env_value(binding.value)

    return environment


def managed_file_secret_names(service) -> tuple[str, ...]:
    return tuple(
        _secret_name(name)
        for name, binding in sorted(service.settings.items())
        if binding.secret is not None
        and (binding.delivery or "env") in {"file", "file_env"}
    )


def generated_env_files(
    services: list[ComposeService],
) -> tuple[GeneratedEnvFile, ...]:
    return tuple(
        GeneratedEnvFile(
            host=service.host,
            stack=service.stack,
            name=service.name,
            path=f"env/{service.name}.env",
            values=service.managed_environment,
        )
        for service in services
        if service.managed_environment
    )


def secret_sources(effective_services, egress=None) -> tuple[SecretSource, ...]:
    sources: list[SecretSource] = []
    for service in sorted(effective_services, key=lambda item: item.id):
        if service.compose is None:
            continue

        runtime_name = service.name
        for name, binding in sorted(service.settings.items()):
            if binding.secret is None:
                continue

            stack_path = _stack_path(service)
            if (binding.delivery or "env") == "env":
                destination = f"{stack_path}/env/{runtime_name}.secrets.env"
                mode = "env"
            else:
                destination = f"{stack_path}/secrets/{_secret_name(name)}"
                mode = "file"

            sources.append(
                SecretSource(
                    source=f"secrets/{binding.secret}.sops.yaml",
                    host=service.host,
                    stack=service.stack,
                    destination=destination,
                    mode=mode,
                    setting=name,
                    secret_name=_secret_name(name),
                )
            )

    # The WireGuard egress secret rides the same manifest as every other
    # secret -- one mechanism, one trust model (T-080) -- which also makes the
    # gateway a recipient in the generated .sops.yaml. owner="system": it is a
    # root-owned daemon config on a gateway that need not have the medusa user.
    if egress is not None:
        sources.append(
            SecretSource(
                source=f"secrets/{egress.wireguard_secret}.sops.yaml",
                host=egress.gateway,
                stack=None,
                destination=f"{egress.gateway}/wireguard/{egress.interface}.conf",
                mode="file",
                setting=egress.interface,
                secret_name=f"wireguard-{egress.interface}",
                owner="system",
            )
        )

    return tuple(sources)


def _managed_secret_env_settings(service) -> dict[str, object]:
    return {
        name: binding
        for name, binding in service.settings.items()
        if binding.secret is not None and (binding.delivery or "env") == "env"
    }


def _stack_path(service) -> str:
    """The path segment a service's secrets live under: its stack, or its host
    for stackless services. Single definition so the manifest destination, the
    Compose references, and the decrypt unit all agree."""
    return service.stack or service.host


def _secret_name(setting_name: str) -> str:
    return setting_name.lower().replace("_", "-")


def _env_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
