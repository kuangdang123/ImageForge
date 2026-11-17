"""模型目录（Real-CUGAN ncnn + APISR ONNX）。

只描述"有哪些模型、支持哪些倍率/降噪"，不碰任何推理细节；
UI 与引擎都从这里取同一份元数据。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    family: str
    backend: str
    model_dir: str            # ncnn: 传给 exe 的 -m；onnx: 模型子目录名（apisr）
    scales: tuple[int, ...]
    default_scale: int
    default_denoise: int
    denoise_levels: tuple[int, ...]
    rank: int                 # 0=最强/最慢，数字越大越轻量
    description: str
    tags: tuple[str, ...] = ()
    onnx_file: str = ""       # backend="onnx" 时使用的 .onnx 文件名
    fixed_input: int = 0      # ONNX 固定输入边长（窗口注意力模型），0=动态

    def denoise_levels_for(self, scale: int) -> tuple[int, ...]:
        """该倍率下真实可用的降噪等级（以磁盘权重为准）。

        降噪档位是（模型, 倍率）的函数：models-pro 2×/3× 只有 -1/0/3，
        models-se 3×/4× 只有 -1/0/3，models-nose 的 2× 只有 0。
        这里委托引擎扫描权重目录，避免把不存在的组合传给 exe（会崩溃）。
        APISR（onnx）模型没有降噪参数，返回空元组。
        """
        if self.backend == "onnx":
            return ()
        from .engine import supported_denoise_levels  # 延迟导入，避免循环依赖

        return supported_denoise_levels(self, int(scale))

    @property
    def pack(self) -> str:
        """该模型属于哪个模型包（下载/安装单位）。"""
        return "apisr" if self.backend == "onnx" else "ncnn"


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="cugan-pro",
        name="Real-CUGAN Pro",
        family="Real-CUGAN",
        backend="ncnn",
        model_dir="models-pro",
        scales=(2, 3),
        default_scale=2,
        default_denoise=2,
        denoise_levels=(-1, 0, 3),
        rank=0,
        description="细节最强，动漫/游戏立绘首选；不支持 4×",
        tags=("detail", "anime", "slow"),
    ),
    ModelSpec(
        id="cugan-se",
        name="Real-CUGAN SE",
        family="Real-CUGAN",
        backend="ncnn",
        model_dir="models-se",
        scales=(2, 3, 4),
        default_scale=2,
        default_denoise=2,
        denoise_levels=(-1, 0, 1, 2, 3),
        rank=1,
        description="均衡型，支持 2/3/4×，通用性最好",
        tags=("balanced", "general"),
    ),
    ModelSpec(
        id="cugan-nose",
        name="Real-CUGAN NoSE",
        family="Real-CUGAN",
        backend="ncnn",
        model_dir="models-nose",
        scales=(1, 2),
        default_scale=2,
        default_denoise=-1,
        denoise_levels=(-1,),
        rank=2,
        description="最轻快，只做 2× 超分，降噪较弱",
        tags=("fast", "light"),
    ),
    ModelSpec(
        id="apisr-grl",
        name="APISR GRL 4×",
        family="APISR",
        backend="onnx",
        model_dir="apisr",
        scales=(4,),
        default_scale=4,
        default_denoise=-1,
        denoise_levels=(),
        rank=3,
        description="1.03M 参数、超轻量，DirectML GPU 上最快；面向动漫截图，固定 4× 输出",
        tags=("fast", "light", "anime"),
        onnx_file="grl4.onnx",
        fixed_input=128,
    ),
    ModelSpec(
        id="apisr-rrdb",
        name="APISR RRDB 4×",
        family="APISR",
        backend="onnx",
        model_dir="apisr",
        scales=(4,),
        default_scale=4,
        default_denoise=-1,
        denoise_levels=(),
        rank=4,
        description="CVPR 2024 论文主模型，质量与速度均衡；固定 4× 输出",
        tags=("balanced", "anime", "general"),
        onnx_file="rrdb4.onnx",
        fixed_input=0,
    ),
    ModelSpec(
        id="apisr-dat",
        name="APISR DAT 4×",
        family="APISR",
        backend="onnx",
        model_dir="apisr",
        scales=(4,),
        default_scale=4,
        default_denoise=-1,
        denoise_levels=(),
        rank=5,
        description="10.89M 参数，画质最好，GPU 上仍较慢；固定 4× 输出",
        tags=("detail", "slow", "anime"),
        onnx_file="dat4.onnx",
        fixed_input=128,
    ),
)

MODEL_BY_ID: dict[str, ModelSpec] = {model.id: model for model in MODELS}

DEFAULT_MODEL_ID = "cugan-pro"

DENOISE_LABELS: dict[int, str] = {
    -1: "保守",
    0: "不降噪",
    1: "轻度降噪",
    2: "中度降噪",
    3: "强降噪",
}


def models_sorted() -> tuple[ModelSpec, ...]:
    """按 rank 升序返回模型（用户看到的"性能从强到弱"顺序）。"""
    return tuple(sorted(MODELS, key=lambda model: (model.rank, model.id)))


def get_model(model_id: str) -> ModelSpec:
    """按 id 取模型；未知 id 回退默认模型。"""
    return MODEL_BY_ID.get(str(model_id), MODEL_BY_ID[DEFAULT_MODEL_ID])


def denoise_levels(model_id: str, scale: int) -> tuple[int, ...]:
    """按（模型, 倍率）查询真实可用的降噪等级。"""
    return get_model(model_id).denoise_levels_for(int(scale))
