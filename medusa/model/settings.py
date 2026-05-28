from medusa.model.services import ComposeService, GeneratedEnvFile, SecretSource


def managed_env_files(service) -> tuple[str, ...]:
    env_files: list[str] = []
    if managed_environment(service):
        env_files.append(f"./env/{service.name}.env")
    if _managed_secret_env_settings(service):
        env_files.append(f"./env/{service.name}.secrets.env")
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


def secret_sources(effective_services) -> tuple[SecretSource, ...]:
    sources: list[SecretSource] = []
    for service in sorted(effective_services, key=lambda item: item.id):
        if service.compose is None:
            continue

        runtime_name = service.name
        for name, binding in sorted(service.settings.items()):
            if binding.secret is None:
                continue

            if (binding.delivery or "env") == "env":
                stack_path = service.stack or service.host
                destination = f"{stack_path}/env/{runtime_name}.secrets.env"
                mode = "env"
            else:
                stack_path = service.stack or service.host
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
    return tuple(sources)


def _managed_secret_env_settings(service) -> dict[str, object]:
    return {
        name: binding
        for name, binding in service.settings.items()
        if binding.secret is not None and (binding.delivery or "env") == "env"
    }


def _secret_name(setting_name: str) -> str:
    return setting_name.lower().replace("_", "-")


def _env_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
