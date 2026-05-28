_BIND_PREFIXES = ("/", "./", "../", "~", "$", "${")


def is_bind_source(value: str) -> bool:
    return value.startswith(_BIND_PREFIXES)


def is_named_compose_volume(source: str) -> bool:
    if is_bind_source(source):
        return False
    return "/" not in source
