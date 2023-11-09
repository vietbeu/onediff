"""Proxy module for converting torch.nn.Module to oneflow.nn.Module"""

import os
import torch
import oneflow as flow
import importlib
from typing import Any
from collections.abc import Iterable
import diffusers
from ._globals import _ONEDIFF_TORCH_TO_OF_CLASS_MAP as __of_mds
from ..import_tools import get_mock_cls_name


__all__ = [
    "proxy_class",
    "ProxySubmodule",
    "replace_obj",
    "replace_func",
    "map_args",
    "get_attr",
]


def proxy_class(cls: type):
    global __of_mds

    if cls.__module__.startswith("torch"):
        mod_name = cls.__module__.replace("torch", "oneflow")
        mod = importlib.import_module(mod_name)
        return getattr(mod, cls.__name__)

    full_cls_name = get_mock_cls_name(cls)

    if full_cls_name in __of_mds:
        return __of_mds[full_cls_name]

    raise RuntimeError(
        f"""
        1. Replace can't find proxy oneflow module for: {str(cls)} \n 
        if not, you need to add it. 
        """
    )


class ProxySubmodule:
    def __init__(self, submod):
        self._oflow_proxy_submod = submod
        self._oflow_proxy_parameters = dict()
        self._oflow_proxy_children = dict()

    def __getitem__(self, index):  # __getitem__
        from .register import torch2oflow

        if isinstance(self._oflow_proxy_submod, Iterable):
            submod = self._oflow_proxy_submod[index]
            return torch2oflow(submod)

        raise RuntimeError(f"can't getitem for: {type(self._oflow_proxy_submod)}")


    def __repr__(self) -> str:
        return " oflow_proxy: " + self._oflow_proxy_submod.__repr__()

    def __getattribute__(self, attribute):
        if attribute.startswith("_oflow_proxy"):
            return object.__getattribute__(self, attribute)
        elif attribute in ["forward", "_conv_forward"]:
            replacement = proxy_class(type(self._oflow_proxy_submod))
            return lambda *args, **kwargs: getattr(replacement, attribute)(
                self, *args, **kwargs
            )
        elif (
            isinstance(
                self._oflow_proxy_submod, diffusers.models.attention_processor.Attention
            )
            and attribute == "get_attention_scores"
        ):
            replacement = proxy_class(type(self._oflow_proxy_submod))
            return lambda *args, **kwargs: getattr(replacement, attribute)(
                self, *args, **kwargs
            )
        elif (
            isinstance(self._oflow_proxy_submod, torch.nn.Linear)
            and attribute == "use_fused_matmul_bias"
        ):
            return (
                self.bias is not None
                and os.getenv("ONEFLOW_KERNEL_ENABLE_FUSED_LINEAR") == "1"
            )
        elif (
            isinstance(self._oflow_proxy_submod, torch.nn.Dropout)
            and attribute == "generator"
        ):
            return flow.Generator()
        elif (
            isinstance(self._oflow_proxy_submod, torch.nn.Conv2d)
            and attribute == "channel_pos"
        ):
            return "channels_first"
        else:
            from .register import torch2oflow


            a = getattr(self._oflow_proxy_submod, attribute)

            if isinstance(a, (torch.nn.parameter.Parameter, torch.Tensor)):
                # TODO(oneflow): assert a.requires_grad == False
                if attribute not in self._oflow_proxy_parameters:
                    a = torch2oflow(a)

                    self._oflow_proxy_parameters[attribute] = a
                else:
                    a = self._oflow_proxy_parameters[attribute]
            elif isinstance(
                a, (torch.nn.Module, torch.nn.ModuleList, torch.nn.Sequential)
            ):
                if attribute not in self._oflow_proxy_children:
                    a = torch2oflow(a)

                    self._oflow_proxy_children[attribute] = a
                else:
                    a = self._oflow_proxy_children[attribute]


            return a

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        replacement = proxy_class(type(self._oflow_proxy_submod))

        if replacement is not None:
            return replacement.__call__(self, *args, **kwargs)
        else:
            raise RuntimeError(
                "can't find oneflow module for: " + str(type(self._oflow_proxy_submod))
            )


############################################## with fx ##############################################


def replace_obj(obj):
    cls = type(obj)
    if cls == torch.dtype:
        return {
            "torch.float16": flow.float16,
            "torch.float32": flow.float32,
            "torch.double": flow.double,
            "torch.int8": flow.int8,
            "torch.int32": flow.int32,
            "torch.int64": flow.int64,
            "torch.uint8": flow.uint8,
        }[str(obj)]
    if cls == torch.fx.immutable_collections.immutable_list:
        return [e for e in obj]
    replacement = proxy_class(cls)
    if replacement is not None:
        if cls in [torch.device]:
            return replacement(str(obj))
        elif cls == torch.nn.parameter.Parameter:
            return flow.utils.tensor.from_torch(obj.data)
        else:
            raise RuntimeError("don't know how to create oneflow obj for: " + str(cls))
    else:
        return obj


def replace_func(func):
    if func == torch.conv2d:
        return flow.nn.functional.conv2d
    if func == torch._C._nn.linear:
        return flow.nn.functional.linear
    if func.__module__.startswith("torch"):
        mod_name = func.__module__.replace("torch", "oneflow")
        mod = importlib.import_module(mod_name)
        return getattr(mod, func.__name__)
    else:
        return func


def map_args(args, kwargs):
    args = [replace_obj(a) for a in args]
    kwargs = dict((k, replace_obj(v)) for (k, v) in kwargs.items())
    return (args, kwargs)


def get_attr(gm, node, torch2flow={}):
    attr = getattr(gm, node.target)
    if attr in torch2flow:
        return torch2flow[attr]
    of_attr = replace_obj(attr)
    torch2flow[attr] = of_attr
    return of_attr