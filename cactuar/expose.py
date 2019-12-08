import sys
from typing import Callable, Optional, Union

from cactuar.models import Methods, RouteMapping


class _Expose:
    _registrar = Methods()

    def __call__(self, func: Callable) -> None:
        self.get(func)

    @classmethod
    def _new_registrar(cls) -> None:
        cls._registrar = Methods()

    @classmethod
    def _register(
        cls,
        func: Callable,
        http_method: str,
        route: Optional[Union[str, Callable]] = None,
    ) -> None:
        if route is None or not isinstance(route, str):
            route = func.__name__
        # noinspection PyProtectedMember
        parents = sys._getframe(3).f_locals
        qual_name = None
        module_name = None
        if "__qualname__" in parents and "__module__" in parents:
            module_name = parents["__module__"]
            qual_name = parents["__qualname__"]
        cls._registrar.get(http_method).mappings.append(
            RouteMapping(route, func, module_name, qual_name, http_method)
        )

    @classmethod
    def get(cls, route: Union[str, Callable] = None) -> Callable:
        def wrapper(wrapped_func: Callable) -> Callable:
            cls._register(wrapped_func, "GET", route)
            return wrapped_func

        if callable(route):
            func = route
            route = None
            return wrapper(func)
        else:
            return wrapper

    @classmethod
    def post(cls, route: Union[str, Callable] = None) -> Callable:
        def wrapper(wrapped_func: Callable) -> Callable:
            cls._register(wrapped_func, "POST", route)
            return wrapped_func

        if callable(route):
            func = route
            route = None
            return wrapper(func)
        else:
            return wrapper

    @classmethod
    def put(cls, route: Union[str, Callable] = None) -> Callable:
        def wrapper(wrapped_func: Callable) -> Callable:
            cls._register(wrapped_func, "PUT", route)
            return wrapped_func

        if callable(route):
            func = route
            route = None
            return wrapper(func)
        else:
            return wrapper

    @classmethod
    def delete(cls, route: Union[str, Callable] = None) -> Callable:
        def wrapper(wrapped_func: Callable) -> Callable:
            cls._register(wrapped_func, "DELETE", route)
            return wrapped_func

        if callable(route):
            func = route
            route = None
            return wrapper(func)
        else:
            return wrapper

    @classmethod
    def patch(cls, route: Union[str, Callable] = None) -> Callable:
        def wrapper(wrapped_func: Callable) -> Callable:
            cls._register(wrapped_func, "PATCH", route)
            return wrapped_func

        if callable(route):
            func = route
            route = None
            return wrapper(func)
        else:
            return wrapper

    @classmethod
    def head(cls, route: Union[str, Callable] = None) -> Callable:
        def wrapper(wrapped_func: Callable) -> Callable:
            cls._register(wrapped_func, "HEAD", route)
            return wrapped_func

        if callable(route):
            func = route
            route = None
            return wrapper(func)
        else:
            return wrapper

    @classmethod
    def options(cls, route: Union[str, Callable] = None) -> Callable:
        def wrapper(wrapped_func: Callable) -> Callable:
            cls._register(wrapped_func, "OPTIONS", route)
            return wrapped_func

        if callable(route):
            func = route
            route = None
            return wrapper(func)
        else:
            return wrapper


expose = _Expose
