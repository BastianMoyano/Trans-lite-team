"""Conservative source-based speaker labels, not voice identification."""
from collections.abc import Mapping


def speaker_for_source(source: str, manual_mapping: Mapping[str, str] | None = None) -> str:
    if manual_mapping and manual_mapping.get(source, '').strip():
        return manual_mapping[source].strip()
    return {'local': 'Local', 'remote': 'Remoto/Desconocido'}.get(source, 'Desconocido')
