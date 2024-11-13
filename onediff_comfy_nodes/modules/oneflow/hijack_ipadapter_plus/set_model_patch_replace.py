import torch

from comfy import model_management
from onediff.infer_compiler.backends.oneflow.transform import torch2oflow
from register_comfy.CrossAttentionPatch import Attn2Replace, ipadapter_attention

from ..patch_management import create_patch_executor, PatchType
from ..utils.booster_utils import clear_deployable_module_cache_and_unbind


def set_model_patch_replace_v2(org_fn, model, patch_kwargs, key):
    apply_patch(org_fn, model, patch_kwargs, key, ipadapter_attention)


@torch.inference_mode()
def apply_patch(org_fn, model, patch_kwargs, key, attention_func=None) -> None:
    diff_model = model.model.diffusion_model
    cache_patch_executor = create_patch_executor(PatchType.CachedCrossAttentionPatch)
    unet_extra_options_patch_executor = create_patch_executor(
        PatchType.UNetExtraInputOptions
    )
    cache_dict = cache_patch_executor.get_patch(diff_model)
    ui_cache_key = create_patch_executor(PatchType.UiNodeWithIndexPatch).get_patch(
        model
    )
    unet_extra_options = unet_extra_options_patch_executor.get_patch(diff_model)

    if "attn2" not in unet_extra_options:
        unet_extra_options["attn2"] = {}

    to = model.model_options["transformer_options"].copy()
    if "patches_replace" not in to:
        to["patches_replace"] = {}
    else:
        to["patches_replace"] = to["patches_replace"].copy()

    if "attn2" not in to["patches_replace"]:
        to["patches_replace"]["attn2"] = {}
    else:
        to["patches_replace"]["attn2"] = to["patches_replace"]["attn2"].copy()

    def split_patch_kwargs(patch_kwargs):
        split1dict = {}
        split2dict = {}
        for k, v in patch_kwargs.items():
            if k in {"cond", "cond_alt", "uncond", "mask", "weight"} or isinstance(
                v, torch.Tensor
            ):
                split1dict[k] = v
            else:
                split2dict[k] = v

            if k in {"sigma_start", "sigma_end"}:
                split1dict[k] = v
                split2dict[k] = v

        # patch for weight
        weight = split1dict["weight"]
        if isinstance(weight, (int, float)):
            weight = torch.tensor(weight)
            split1dict["weight"] = weight.to(model_management.get_torch_device())

        # https://github.com/cubiq/ComfyUI_IPAdapter_plus/blob/2ff4fc482029d408cfd5fa05522ca822b2c2e33c/IPAdapterPlus.py#L252-L253
        if isinstance(weight, dict):
            for k, v in weight.items():
                weight[k] = torch.tensor(v).to(model_management.get_torch_device())

        return split1dict, split2dict

    new_patch_kwargs, patch_kwargs = split_patch_kwargs(patch_kwargs)
    # update patch_kwargs
    if key in cache_dict:
        try:
            attn2_m = cache_dict[key]
            index = attn2_m.cache_map.get(ui_cache_key, None)
            if index is not None:
                unet_extra_options["attn2"][attn2_m.forward_patch_key][
                    index
                ] = new_patch_kwargs

                to["patches_replace"]["attn2"][key] = attn2_m
                model.model_options["transformer_options"] = to
                return

        except Exception as e:
            clear_deployable_module_cache_and_unbind(model)

    if key not in to["patches_replace"]["attn2"]:
        if key not in cache_dict:
            attn2_m_pt = Attn2Replace(attention_func, **patch_kwargs)
            attn2_m_of = torch2oflow(attn2_m_pt, bypass_check=True)

            cache_dict[key] = attn2_m_of
            attn2_m: Attn2Replace = attn2_m_of
            index = len(attn2_m.callback) - 1
            attn2_m.cache_map[ui_cache_key] = index
            unet_extra_options["attn2"][attn2_m.forward_patch_key] = [new_patch_kwargs]

            # QuantizedInputPatch
            attn2_m._bind_model = attn2_m_pt
        else:
            attn2_m = cache_dict[key]

        to["patches_replace"]["attn2"][key] = attn2_m
        model.model_options["transformer_options"] = to
    else:
        attn2_m: Attn2Replace = to["patches_replace"]["attn2"][key]
        attn2_m.add(torch2oflow(attention_func), **torch2oflow(patch_kwargs))
        unet_extra_options["attn2"][attn2_m.forward_patch_key].append(
            new_patch_kwargs
        )  # update last patch
        attn2_m.cache_map[ui_cache_key] = len(attn2_m.callback) - 1

        if attn2_m.get_bind_model() is not None:
            bind_model: Attn2Replace = attn2_m.get_bind_model()
            bind_model.add(attention_func, **patch_kwargs)

    if not create_patch_executor(PatchType.QuantizedInputPatch).check_patch():
        create_patch_executor(PatchType.QuantizedInputPatch).set_patch()
