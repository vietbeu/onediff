import os
from functools import singledispatchmethod

from comfy.model_patcher import ModelPatcher
from comfy.controlnet import ControlLora, ControlNet
from onediff.infer_compiler.backends.oneflow import (
    OneflowDeployableModule as DeployableModule,
)

from ..booster_interface import BoosterExecutor


class PatchBoosterExecutor(BoosterExecutor):
    @singledispatchmethod
    def execute(self, model, ckpt_name=None):
        return model

    def _set_batch_size_patch(self, diff_model: DeployableModule, latent_image):
        batch_size = latent_image["samples"].shape[0]
        if isinstance(diff_model, DeployableModule):
            file_path = diff_model.get_graph_file()
            if file_path is None:
                return diff_model

            file_dir = os.path.dirname(file_path)
            file_name = os.path.basename(file_path)
            names = file_name.split("_")
            key, is_replace = "bs=", False
            for i, name in enumerate(names):
                if key in name:
                    names[i] = f"{key}{batch_size}"
                    is_replace = True
            if not is_replace:
                names = [f"{key}{batch_size}"] + names

            new_file_name = "_".join(names)
            new_file_path = os.path.join(file_dir, new_file_name)

            diff_model.set_graph_file(new_file_path)
        else:
            print(f"Warning: model is not a {DeployableModule}")
        return diff_model

    @execute.register(ModelPatcher)
    def _(self, model, **kwargs):
        latent_image = kwargs.get("latent_image", None)
        if latent_image:
            diff_model = model.model.diffusion_model
            self._set_batch_size_patch(diff_model, latent_image)
        return model


class PatchUnetGraphCacheExecutor(BoosterExecutor):
    @singledispatchmethod
    def execute(self, model, ckpt_name=None):
        print(f"Warning: cache manager {type(model)} is not supported")
        return model

    @execute.register(ModelPatcher)
    def _(
        self,
        model,
        cache_dir,
        filename,
        custom_suffix=".graph",
        overwrite=False,
        **kwargs,
    ):
        # model.model.diffusion_model = self.compile_fn(model.model.diffusion_model)
        if not isinstance(model.model.diffusion_model, DeployableModule):
            return model

        diff_model: DeployableModule = model.model.diffusion_model
        module_type = type(model.model).__name__
        graph_file_name = f"{module_type}{os.sep}{filename}{custom_suffix}"

        compiled_options = diff_model._deployable_module_options
        compiled_options.graph_file = os.path.join(cache_dir, graph_file_name)
        if overwrite:
            print(f"Warning: overwrite cache file {compiled_options.graph_file}")
            os.remove(compiled_options.graph_file)

        compiled_options.skip_graph_file_safety_check = True
        return model

    @execute.register(VAE)
    def _(self, model, cache_dir, filename, overwrite=False, **kwargs):
        # model.first_stage_model = self.compile_fn(model.first_stage_model)
        return model

    @execute.register(ControlNet)
    def _(self, model, cache_dir, filename, overwrite=False, **kwargs):
        # torch_model = model.control_model
        # compiled_model = self.compile_fn(torch_model)
        # model.control_model = compiled_model
        return model

    @execute.register(ControlLora)
    def _(self, model, cache_dir, filename, overwrite=False, **kwargs):
        # torch_model = model.control_model
        # compiled_model = self.compile_fn(torch_model)
        # model.control_model = compiled_model
        return model
