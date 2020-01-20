import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from functools import partial
from pathlib import Path
from typing import Any, Dict, Union

from cactuar.models import StrOrBytes


class DataClassEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Dict[str, Any]:
        if is_dataclass(obj):
            return asdict(obj)
        return json.JSONEncoder.default(self, obj)


def decode_bytes_to_str(value: StrOrBytes) -> str:
    """
    Will convert any byte strings to UTF-8 strings
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return value


def format_data(kwargs: Dict) -> Dict:
    """
    De-lists any single-item lists and scrubs the values

    So {b'foo': [b'bar']} would become {'foo': 'bar'}
    """
    new_kwargs = {}
    for key, value in kwargs.items():
        if isinstance(value, list):
            for index, val in enumerate(value):
                value[index] = decode_bytes_to_str(val)
        if len(value) == 1:
            value = decode_bytes_to_str(value[0])
        new_kwargs[decode_bytes_to_str(key)] = value
    return new_kwargs


class File:
    def __init__(self, path: Union[str, Path]) -> None:
        if isinstance(path, str):
            path = Path(path)
        self.path = path
        self.chunk_size = 64

    async def read(self) -> bytes:
        chunks = []
        loop = asyncio.get_event_loop()
        pool = ThreadPoolExecutor()
        open_file = self.path.open("rb")
        read_func = partial(open_file.read, self.chunk_size)
        while True:
            chunk = await loop.run_in_executor(pool, read_func)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
