import json
import urllib.parse
from collections import namedtuple
from dataclasses import is_dataclass
import inspect
from io import IOBase, TextIOBase
from typing import TYPE_CHECKING, Dict, Optional, Type, List, Callable, Any

import dacite  # type: ignore

from cactuar.exceptions import HTTPError, FigureItOutLaterException
from cactuar.expose import _Expose
from cactuar.request import Request
from cactuar.response import Response
from cactuar.types import Branch, Leaf, TreePart
from cactuar.util import DataClassEncoder, format_data

if TYPE_CHECKING:
    from cactuar.app import App


class Router:
    def __init__(self, app: "App"):
        self.app = app
        self.root: Optional[Type] = None

    async def handle_request(self, request: Request) -> Response:
        pass


class MethodRouter(Router):
    def __init__(self, app):
        super().__init__(app)
        self._root: Optional[Type] = None
        self._tree: Optional[TreePart] = None
        self.method_registration = _Expose._registrar

    @property
    def root(self):
        return self._root

    @root.setter
    def root(self, root: Type):
        self._root = root
        self._tree = Branch("", root, self.build_tree(root))

    async def handle_request(self, request: Request) -> Response:
        response = Response()
        response.body = await self._dispatch(request)
        return response

    async def _dispatch(self, request: Request) -> bytes:
        func = self.get_func(request)
        return await self.call_func(request, func)

    @staticmethod
    async def get_kwargs(request: Request) -> Dict:
        kwargs: Dict[str, Any] = {}
        body = await request.get_body()
        try:
            content_type = request.headers["content-type"]
        except KeyError:
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
        # arg_types.positional: List[Any] = args
        # arg_types.keyword: Dict[str, Any] = {}
        params = inspect.signature(func).parameters
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
            return self.format_response_body(result)

        raise HTTPError(404)

    @staticmethod
    def format_response_body(result) -> bytes:
        if isinstance(result, (dict, list)) or is_dataclass(result):
            return json.dumps(result, cls=DataClassEncoder).encode("utf-8")
        if isinstance(result, TextIOBase):
            return result.read().encode("utf-8")
        if isinstance(result, IOBase):
            return b"".join(result.readlines())
        raise NotImplementedError

    def get_func(self, request: Request) -> Callable:
        http_method = request.method
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
        if route:
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
                    children.append(Branch(key, value, self.build_tree(value)))
                elif inspect.ismethod(value) or inspect.isfunction(value):
                    mappings = [
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
                    ]
                    mappings = [mapp for mapp in mappings if mapp]
                    for mapp in mappings:
                        children.append(Leaf(mapp.route, cls, mapp))

        return children

    # def get_url(self, request: Request, func: Callable) -> str:
    #     http_method = request.method
    #     mapping = self.method_registration.get(http_method).get_map_by_func(
    #         func.__name__
    #     )
    #     reversed_url = mapping.route
    #     if reversed_url == self.DEFAULT_ROUTE:
    #         reversed_url = ""
    #     reversed_url = f"{self.get_route()}/{reversed_url}"
    #     parent = list(self.route_parents.values())[0]
    #     while True:
    #         reversed_url = f"{parent.get_route()}/{reversed_url}"
    #         if hasattr(parent, "route_parents"):
    #             parent = list(parent.route_parents.values())[0]
    #         else:
    #             break
    #     return f"/{reversed_url}"
    #
    # @classmethod
    # def get_route(cls):
    #     if hasattr(cls, "ROUTE"):
    #         route = cls.ROUTE
    #     else:
    #         route = cls.__name__.replace("Handler", "").lower()
    #     return route
