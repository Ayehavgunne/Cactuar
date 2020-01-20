import inspect
import json
import urllib.parse
from collections import namedtuple
from dataclasses import is_dataclass
from io import IOBase, TextIOBase
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Type, Union

import dacite

from cactuar import content_types
from cactuar.contexed.request import Request
from cactuar.contexed.response import Response
from cactuar.exceptions import FigureItOutLaterException, HTTPError
from cactuar.expose import _Expose
from cactuar.models import Branch, Leaf, TreePart
from cactuar.util import DataClassEncoder, File, format_data

if TYPE_CHECKING:
    from cactuar.app import App


class Router:
    def __init__(self, app: "App"):
        self.app = app
        self._response: Response = Response()

    async def format_response_body(self, result: Any) -> bytes:
        if isinstance(result, (dict, list)) or is_dataclass(result):
            if not self._response.content_type:
                self._response.content_type = "application/json"
            return json.dumps(result, cls=DataClassEncoder).encode("utf-8")
        if isinstance(result, TextIOBase):
            self.app.app_logger.warning(
                "open().read() is a blocking operation! Use cactuar.File() instead."
            )
            return result.read().encode("utf-8")
        if isinstance(result, IOBase):
            self.app.app_logger.warning(
                "open().read() is a blocking operation! Use cactuar.File() instead."
            )
            return b"".join(result.readlines())
        if isinstance(result, File):
            return await result.read()
        if isinstance(result, str):
            return result.encode("utf-8")
        if isinstance(result, bytes):
            return result
        raise NotImplementedError

    async def handle_request(self, request: Request, response: Response) -> Response:
        raise NotImplementedError


class MethodRouter(Router):
    def __init__(self, app: "App"):
        super().__init__(app)
        # noinspection PyProtectedMember
        self.method_registration = _Expose._registrar
        self._root: Optional[Type] = None
        self._tree: Optional[TreePart] = None

    @property
    def root(self) -> Optional[Type]:
        return self._root

    @root.setter
    def root(self, root: Type) -> None:
        self._root = root
        self._tree = Branch("", root, self.build_tree(root))

    def set_content_type_from_annotation(self, annotation) -> None:  # type: ignore
        if not self._response.content_type and annotation in content_types.ContentTypes:
            self._response.content_type = content_types.CONTENT_TYPE_MAP[annotation]

    async def handle_request(self, request: Request, response: Response) -> Response:
        self._response = response
        response.status = 200
        response.body = await self._dispatch(request)
        return response

    async def _dispatch(self, request: Request) -> bytes:
        func = self.get_func(request)
        return await self.call_func(request, func)

    @staticmethod
    async def get_kwargs(request: Request) -> Dict:
        kwargs: Dict[str, Any] = {}
        body = await request.get_body()
        content_type = request.headers["content-type"]
        if content_type is None:
            content_type = ""

        if body:
            if "json" in content_type:
                kwargs.update(json.loads(body))
            if content_type == "application/x-www-form-urlencoded":
                kwargs.update(format_data(urllib.parse.parse_qs(body)))

        if request.query_string:
            kwargs.update(request.query_string)
        return kwargs

    @staticmethod
    def get_args(request: Request) -> List[Any]:
        if request.current_route is not None:
            args: List[Any] = [request.current_route.class_instance]
        else:
            args = []
        # noinspection PyProtectedMember
        args.extend(
            [
                path
                for path in request._unsearched_path.split("/")
                if path and path != "index"
            ]
        )
        return args

    async def call_func(self, request: Request, func: Callable) -> bytes:
        kwargs = await self.get_kwargs(request)
        args = self.get_args(request)

        ArgTypes = namedtuple("ArgTypes", "positional keyword")
        arg_types = ArgTypes(args, {})
        sig = inspect.signature(func)
        params = sig.parameters
        return_annotation = getattr(sig, "return_annotation")
        if return_annotation:
            self.set_content_type_from_annotation(return_annotation)
        var_pos = False
        var_keyword = False
        for name, param in params.items():
            if (
                kwargs
                and hasattr(param, "annotation")
                and is_dataclass(param.annotation)
            ):
                instance = dacite.from_dict(param.annotation, kwargs)
                for key in list(kwargs.keys()):
                    if hasattr(instance, key):
                        del kwargs[key]
                arg_types.positional.append(instance)
            if name in kwargs:
                if param.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                ):
                    arg_types.positional.append(kwargs.pop(name))
                else:
                    arg_types.keyword[name] = kwargs.pop(name)
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                var_pos = True
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                var_keyword = True

        for key in list(kwargs.keys()):
            if var_pos:
                arg_types.positional.append(kwargs.pop(key))
            elif var_keyword:
                arg_types.keyword[key] = kwargs.pop(key)

        if func is not None and not kwargs:
            result = await func(*arg_types.positional, **arg_types.keyword)
            return await self.format_response_body(result)

        raise HTTPError(404)

    def get_func(self, request: Request) -> Callable:
        http_method = request.method
        if isinstance(request.path, bytes):
            url_path = request.path.decode("utf-8")
        else:
            url_path = request.path
        paths = [urllib.parse.unquote(pth) for pth in url_path.split("/") if pth != ""]
        paths.append("index")
        if self._tree is None:
            raise FigureItOutLaterException
        route = self._tree
        level = 0
        while isinstance(route, Branch):
            if len(paths) <= level:
                raise HTTPError(404)
            route = self.match_route(paths[level], route.children, http_method)
            level += 1
        if route and isinstance(route, Leaf):
            request._unsearched_path = "/".join(paths[level:])
            request.current_route = route
            if route.route_mapping.func is not None:
                return route.route_mapping.func
            else:
                raise FigureItOutLaterException
        else:
            raise HTTPError(404)

    @staticmethod
    def match_route(
        current_path: str, routes: List[TreePart], http_method: str
    ) -> TreePart:
        for route in routes:
            if route.route == current_path:
                if isinstance(route, Leaf):
                    if route.route_mapping.http_method == http_method:
                        return route
                elif isinstance(route, Branch):
                    return route
        raise HTTPError(404)

    def build_tree(self, cls: Type) -> List[TreePart]:
        children: List[TreePart] = []

        for key, value in cls.__class__.__dict__.items():
            if not key.startswith("_"):
                if (
                    key.islower()
                    and hasattr(value, "__class__")
                    and inspect.isclass(value.__class__)
                    and not inspect.ismethod(value)
                    and not inspect.isfunction(value)
                ):
                    childs = self.build_tree(value)
                    branch = Branch(key, value, childs)
                    for child in childs:
                        child.parent = branch
                    children.append(branch)
                elif inspect.ismethod(value) or inspect.isfunction(value):
                    mappings = {
                        self.method_registration.GET.get_map_by_func(
                            value.__name__, cls.__class__.__name__
                        ),
                        self.method_registration.POST.get_map_by_func(
                            value.__name__, cls.__class__.__name__
                        ),
                        self.method_registration.PUT.get_map_by_func(
                            value.__name__, cls.__class__.__name__
                        ),
                        self.method_registration.PATCH.get_map_by_func(
                            value.__name__, cls.__class__.__name__
                        ),
                        self.method_registration.DELETE.get_map_by_func(
                            value.__name__, cls.__class__.__name__
                        ),
                        self.method_registration.HEAD.get_map_by_func(
                            value.__name__, cls.__class__.__name__
                        ),
                        self.method_registration.OPTIONS.get_map_by_func(
                            value.__name__, cls.__class__.__name__
                        ),
                    }
                    mappings = {mapping for mapping in mappings if mapping}
                    for mapping in mappings:
                        children.append(Leaf(mapping.route, cls, mapping))

        return children


class StaticRouter(Router):
    def __init__(self, app: "App", path_root: Union[str, Path]):
        super().__init__(app)
        if isinstance(path_root, str):
            path_root = Path(path_root)
        self.path_root = path_root.absolute().resolve()

    async def handle_request(self, request: Request, response: Response) -> Response:
        self._response = response
        response.status = 200
        response.body = await self._dispatch(request)
        return response

    async def _dispatch(self, request: Request) -> bytes:
        pass
