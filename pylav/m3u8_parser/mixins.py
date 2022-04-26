from __future__ import annotations

import os

from pylav.m3u8_parser.parser import is_url, urljoin


def _urijoin(base_uri: str, path: str) -> str:
    if is_url(base_uri):
        if base_uri[-1] != "/":
            base_uri += "/"
        return urljoin(base_uri, path)
    else:
        return os.path.normpath(os.path.join(base_uri, path.strip("/")))


class BasePathMixin:
    @property
    def absolute_uri(self) -> str | None:
        if self.uri is None:
            return None
        if is_url(self.uri):
            return self.uri
        else:
            if self.base_uri is None:
                raise ValueError("There can not be `absolute_uri` with no `base_uri` set")
            return _urijoin(self.base_uri, self.uri)

    @property
    def base_path(self) -> str | None:
        if self.uri is None:
            return None
        return os.path.dirname(self.get_path_from_uri())

    def get_path_from_uri(self) -> str:
        """Some URIs have a slash in the query string."""
        return self.uri.split("?")[0]

    @base_path.setter
    def base_path(self, newbase_path: str) -> None:
        if self.uri is not None:
            if not self.base_path:
                self.uri = f"{newbase_path}/{self.uri}"
            else:
                self.uri = self.uri.replace(self.base_path, newbase_path)


class GroupedBasePathMixin:
    def _set_base_uri(self, new_base_uri: str) -> str:
        for item in self:
            item.base_uri = new_base_uri

    base_uri = property(None, _set_base_uri)

    def _set_base_path(self, newbase_path: str) -> str:
        for item in self:
            item.base_path = newbase_path

    base_path = property(None, _set_base_path)
