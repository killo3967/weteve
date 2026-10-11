import logging
import re
from pathlib import Path

import requests

from src.domain.entities.channel import Channel
from src.domain.ports.i_playlist_repo import IPlaylistRepository


class FileM3URepository(IPlaylistRepository):
    """Adaptador que gestiona la persistencia y carga de canales desde archivos M3U."""
    
    def get_channels(self, source: str) -> list[Channel]:
        """Carga los canales desde una URL o un archivo local."""
        m3u_content = self._get_content(source)
        return self._parse_m3u(m3u_content)

    def _get_content(self, source: str) -> list[str]:
        """Obtiene las líneas del contenido M3U."""
        if source.startswith('http'):
            # Descarga remota
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}
                # requests detecta proxies de variables de entorno automáticamente
                response = requests.get(source, headers=headers, timeout=15)
                response.raise_for_status()
                return response.text.splitlines()
            except Exception as e:
                logging.error(f"Error descargando M3U: {e}")
                return []
        else:
            # Archivo local
            path = Path(source)
            if not path.exists():
                return []
            with open(path, 'r', encoding='utf-8') as f:
                return f.readlines()

    def _parse_m3u(self, lines: list[str]) -> list[Channel]:
        """Lógica de parseo de M3U extraída del código original."""
        channels = []
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('#EXTINF'):
                info = self._parse_extinf(line)
                
                # Buscar la URL real
                j = i + 1
                while j < len(lines) and (lines[j].strip().startswith('#') or not lines[j].strip()):
                    j += 1
                
                url = lines[j].strip() if j < len(lines) else ""
                
                if url:
                    channels.append(Channel(
                        name=info.get('tvg-name') or info.get('display-name') or info.get('tvg-id') or "Canal sin nombre",
                        url=url,
                        group=info.get('group-title', 'Otros'),
                        logo_url=info.get('tvg-logo'),
                        tvg_id=info.get('tvg-id')
                    ))
                i = j
            else:
                i += 1
        return channels

    def _parse_extinf(self, line: str) -> dict:
        """Extrae atributos de la línea #EXTINF."""
        patterns = {
            'tvg-id': r'tvg-id="(.*?)"',
            'tvg-name': r'tvg-name="(.*?)"',
            'tvg-logo': r'tvg-logo="(.*?)"',
            'group-title': r'group-title="(.*?)"'
        }
        results = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, line)
            results[key] = match.group(1) if match else ""

        # Extraer nombre tras la última coma (formato: ...,Channel Name)
        # Útil para listas que no usan tvg-name (ej: Pluto TV)
        comma_idx = line.rfind(',')
        if comma_idx != -1:
            display_name = line[comma_idx + 1:].strip()
            if display_name:
                results['display-name'] = display_name

        return results
