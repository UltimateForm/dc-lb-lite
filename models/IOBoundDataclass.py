from dataclasses import dataclass
import json
import os
import aiofiles
from aiofiles import os as aos
from dacite import from_dict


@dataclass
class IOBoundDataclass:
    @classmethod
    def delete(cls):
        os.remove(cls.get_path())

    @classmethod
    async def adelete(cls):
        await aos.remove(cls.get_path())

    @classmethod
    def exists(cls):
        return os.path.exists(cls.get_path())

    @classmethod
    async def aexists(cls):
        exists = await aos.path.exists(cls.get_path())
        return exists

    def as_dict(self):
        return self.__dict__.copy()

    def save(self):
        with open(self.get_path(), "w", encoding="utf8") as config_file:
            json.dump(self.as_dict(), config_file)

    async def asave(self):
        async with aiofiles.open(self.get_path(), "w", encoding="utf8") as config_file:
            await config_file.write(json.dumps(self.as_dict()))

    @classmethod
    def _load(cls):
        if not cls.exists():
            return cls()
        json_data: dict = {}
        path = cls.get_path()
        with open(path, "r", encoding="utf8") as config_file:
            json_data = json.loads(config_file.read())
        config_data = from_dict(data_class=cls, data=json_data)
        return config_data

    @classmethod
    async def _aload(cls):
        exists = await cls.aexists()
        if not exists:
            return cls()
        path = cls.get_path()
        json_data: dict = {}
        async with aiofiles.open(path, "r", encoding="utf8") as config_file:
            content = await config_file.read()
            json_data = json.loads(content)
        config_data = from_dict(data_class=cls, data=json_data)
        return config_data

    @classmethod
    def load(cls):
        return cls._load()

    @classmethod
    async def afile_size(cls):
        exists = await cls.aexists()
        if not exists:
            return 0
        file_size = await aos.path.getsize(cls.get_path())
        return file_size

    @classmethod
    def aload(cls):
        return cls._aload()

    @classmethod
    def get_path(cls) -> str:
        raise NotImplementedError(
            "get_path classmethod must be implemented in child class"
        )
