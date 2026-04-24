# 当前工作站上的 ASR 本地部署建议

> 机器检查时间：2026-04-22
> 适用对象：当前这台 `Ubuntu 22.04 / Quadro P5000 / 62 GiB RAM` 的工作站
> 结论先行：**这台机器现在最合适的落地方案是 `Qwen3-ASR-0.6B + transformers backend`，不建议走 vLLM，也不建议把 `Qwen3-ASR-1.7B` 作为第一落地目标。**  
> 如果你的业务极度依赖中文热词和行业词，再补测 `Fun-ASR-Nano` 作为备选。

---

## 1. 这台机器的实际情况

我基于本机命令行检查到的关键信息如下：

- 系统：Ubuntu 22.04，内核 `6.8.0-52-generic`
- CPU：`Intel Core i7-7800X`，6 核 12 线程
- 内存：`62 GiB`
- 磁盘：当前仓库所在分区剩余约 `349 GiB`
- Python：系统默认 `3.11.4`
- GPU：`NVIDIA Quadro P5000`
- GPU 代际：Pascal，`Compute Capability 6.1`
- 显存：官方规格 `16 GB GDDR5X`

额外发现两个很重要的问题：

- 当前系统存在 **NVIDIA Driver / NVML library version mismatch**
  - `nvidia-smi` 返回：
    - `Failed to initialize NVML: Driver/library version mismatch`
    - `NVML library version: 535.288`
  - `/proc/driver/nvidia/version` 显示当前内核模块版本是 `535.183.01`
- 系统明确提示：
  - `*** System restart required ***`

这基本说明：**用户态 NVIDIA 库已经升级到 535.288，但当前正在运行的内核模块还是旧版本 535.183。**

---

## 2. 这意味着什么

### 2.1 现在不是“没 GPU”，而是 GPU 状态不健康

这台机器不是没有显卡，而是显卡运行时当前不一致。

- `lspci` 能识别到 `Quadro P5000`
- `lsmod` 也能看到 `nvidia` 相关模块
- 但 `nvidia-smi` 无法正常工作

对模型部署的影响是：

- 你现在 **不适合直接开始正式部署**
- 先修 GPU 运行时状态，否则后续 PyTorch/CUDA 很容易出现奇怪问题

---

### 2.2 这张卡不适合走 vLLM 路线

即使把驱动修好，这张卡也不适合当前官方推荐的 vLLM 路线。

原因有两个：

#### 原因 1：vLLM 官方 GPU 要求不满足

vLLM 官方文档当前明确写的是：

- NVIDIA GPU 要求：**compute capability 7.5 or higher**

而 Quadro P5000 属于 Pascal 架构，NVIDIA 官方 CUDA 能力列表里是：

- **Compute Capability 6.1**

所以对这台机器来说：

- **不要把 Qwen3-ASR 的 vLLM backend 当成主方案**
- `qwen-asr-serve` 那条封装 `vllm serve` 的服务化路径，也不应该是你在这台机器上的首选

#### 原因 2：FlashAttention-2 也不适合这张卡

FlashAttention 官方 README 当前说明：

- FlashAttention-2 的 CUDA 支持面向：
  - **Ampere**
  - **Ada**
  - **Hopper**
  - Turing 需走单独仓库

Quadro P5000 是 **Pascal**，不在支持范围内。

这意味着：

- Qwen3-ASR 示例里注释掉的 `attn_implementation="flash_attention_2"`，在这台机器上不要开启
- 强行追求最新 kernel/attention 优化路线，收益不大，坑反而多

---

## 3. 这台机器上应该怎么选模型

## 3.1 最推荐的方案

### 方案 A：Qwen3-ASR-0.6B + transformers backend

这是我认为最适合当前机器的默认方案。

原因：

- `Qwen3-ASR-0.6B` 官方包 `qwen-asr` 支持 `Python >= 3.9`，你当前 `Python 3.11.4` 可以直接用
- 不依赖 vLLM 也能跑
- 0.6B 体量对 `16GB` 显存更稳妥
- 即使不开 FlashAttention-2，也比 1.7B 更现实
- 你后续如果真需要时间戳，还可以再接 `Qwen3-ForcedAligner-0.6B`

适合当前机器的定位：

- 单机本地转写
- 小规模服务
- 批量离线转写
- 开发环境 PoC

不适合期待：

- 高并发在线服务
- 最新 vLLM 路线
- 大吞吐 streaming 服务

---

## 3.2 备选方案

### 方案 B：Fun-ASR-Nano

如果你的重点是：

- 中文业务
- 行业词很多
- 强依赖显式 hotword
- 会议/客服/政企类中文语音

那这台机器上，`Fun-ASR-Nano` 可以作为一个很值得补测的方案。

为什么它是备选而不是默认首选：

- 当前新仓库的时间戳仍在 TODO
- 工具链没有 Qwen3-ASR 完整
- 服务化、对齐、标准化部署体验不如 Qwen3-ASR 成熟

但为什么还值得测：

- 800M 规模在这张 16GB 卡上是现实的
- 对中文场景和热词很友好
- 如果你的样本非常偏中文行业语料，它可能比通用模型更贴业务

---

## 3.3 当前不建议的方案

### 不建议 1：Qwen3-ASR-1.7B 作为第一落地方案

原因：

- 1.7B 体量显著更大
- 这张卡是 Pascal，不支持你最想要的现代加速路径
- 即使跑得起来，也更容易卡在显存、长音频、并发和速度上
- 你如果还想叠加 `Qwen3-ForcedAligner-0.6B`，资源压力会更大

我的判断：

- **它可以作为“后续挑战项”**
- **不应该作为这台机器今天的首发方案**

### 不建议 2：Qwen3-ASR 的 vLLM backend

原因前面已经说了：

- 官方要求 `compute capability 7.5+`
- 这台卡是 `6.1`

所以这不是“可能慢一点”，而是 **从官方支持矩阵上就不匹配**。

### 不建议 3：CPU-only 正式部署

这台机器 CPU 是 6C12T，虽然能做实验，但不适合把这两类模型当成正式 CPU 服务方案。

如果 GPU 修不好：

- 只建议做短音频试跑和接口联调
- 不建议作为长期运行形态

---

## 4. 针对这台机器的最终推荐顺序

### 推荐顺序

1. **Qwen3-ASR-0.6B**
2. **Fun-ASR-Nano**
3. `Qwen3-ASR-0.6B + Qwen3-ForcedAligner-0.6B`
4. `Qwen3-ASR-1.7B`

### 为什么是这个顺序

#### 第一名：Qwen3-ASR-0.6B

- 功能最均衡
- 当前机器最容易跑起来
- 风险最低
- 后续扩展路径最好

#### 第二名：Fun-ASR-Nano

- 如果你的音频很中文、很行业、很依赖热词，它可能在业务效果上更接近你的真实需求
- 但部署和后续扩展性不如 Qwen3-ASR

#### 第三名：Qwen3-ASR + ForcedAligner

- 如果你明确需要时间戳，这组能力是最完整的
- 但在这张卡上，资源压力会比单模型大
- 我建议先把纯转写跑通，再补 aligner

#### 第四名：Qwen3-ASR-1.7B

- 更强，但和这台机器的硬件代际不匹配

---

## 5. 先修什么：GPU 驱动状态

## 5.1 我认为最可能的原因

从当前包状态看：

- 已安装的用户态包大多是 `535.288.01`
- 当前运行的内核模块是 `535.183.01`
- 系统同时提示 `System restart required`

这非常像：

- 驱动包已经升级
- 但系统还没重启到和新驱动一致的状态

### 第一动作

先做一次 **重启**。

重启后优先验证：

```bash
uname -r
nvidia-smi
```

如果重启后 `nvidia-smi` 正常，就先不要再折腾驱动。

---

## 5.2 如果重启后仍然 mismatch

再考虑做一次 535 驱动重装，并重启。

建议先做保守路线：

```bash
sudo apt update
sudo apt install --reinstall \
  nvidia-driver-535 \
  nvidia-dkms-535 \
  nvidia-utils-535 \
  libnvidia-compute-535 \
  nvidia-compute-utils-535
sudo reboot
```

重启后再验证：

```bash
nvidia-smi
cat /proc/driver/nvidia/version
```

### 不建议现在做的事

- 不建议混用 NVIDIA 官方 `.run` 安装器
- 不建议一上来就 purge 全套驱动
- 不建议在驱动没稳定前先装一堆 CUDA/PyTorch 版本

先把 `nvidia-smi` 跑通，再装模型环境。

---

## 6. 这台机器上的推荐部署路线

## 路线 1：最推荐

### Qwen3-ASR-0.6B，走 transformers backend

这是当前机器最稳的路线。

### 环境建议

虽然 `qwen-asr` 官方包要求是 `Python >= 3.9`，你当前的 `3.11.4` 已经够，但我仍建议：

- 给 ASR 单独建一个虚拟环境
- 不和当前项目的 Python 环境混用

示例：

```bash
mkdir -p ~/asr-envs
cd ~/asr-envs
python3 -m venv qwen3-asr-0.6b
source qwen3-asr-0.6b/bin/activate
python -m pip install -U pip setuptools wheel
pip install -U qwen-asr
```

### 最小可用推理示例

```python
import torch
from qwen_asr import Qwen3ASRModel

model = Qwen3ASRModel.from_pretrained(
    "Qwen/Qwen3-ASR-0.6B",
    dtype=torch.float16,
    device_map="cuda:0",
    max_inference_batch_size=4,
    max_new_tokens=256,
)

results = model.transcribe(
    audio="/absolute/path/test.wav",
    language=None,
)

print(results[0].language)
print(results[0].text)
```

### 这里为什么用 `torch.float16`

官方示例大量使用 `bfloat16`，但这张卡是 Pascal，**不要默认按新一代卡的 BF16/FlashAttention 习惯来配**。  
在这台机器上，更稳妥的思路是：

- 不开 `flash_attention_2`
- 先用 `float16`
- 小 batch 起步

### 初始参数建议

- `max_inference_batch_size=1` 或 `4`
- `max_new_tokens=256`
- 先只跑单音频
- 先别接 forced aligner

跑稳后再加大。

---

## 路线 2：中文热词场景

### Fun-ASR-Nano

如果你想先测中文行业热词效果，可以单独建一个环境：

```bash
mkdir -p ~/asr-envs
cd ~/asr-envs
python3 -m venv fun-asr-nano
source fun-asr-nano/bin/activate
python -m pip install -U pip setuptools wheel
git clone https://github.com/FunAudioLLM/Fun-ASR.git
cd Fun-ASR
pip install -r requirements.txt
```

最小推理路径沿官方示例走：

```python
from funasr import AutoModel

model = AutoModel(
    model="FunAudioLLM/Fun-ASR-Nano-2512",
    trust_remote_code=True,
    remote_code="./model.py",
    device="cuda:0",
    hub="hf",
)

res = model.generate(
    input=["/absolute/path/test.wav"],
    cache={},
    batch_size=1,
    hotwords=["你最重要的业务词"],
    language="中文",
    itn=True,
)

print(res[0]["text"])
```

### 适合什么时候先测它

- 你的音频 80% 以上是中文
- 术语词表是刚需
- 你可以暂时不要时间戳

---

## 7. 这台机器上不建议立刻做的能力

### 7.1 不建议先上流式

Qwen3-ASR 的 streaming 当前只支持 vLLM backend，而这条路和这张卡不匹配。

所以在这台机器上：

- **先做离线转写**
- 不要把实时 streaming 当第一阶段目标

### 7.2 不建议一开始就追时间戳

时间戳需要额外 aligner 资源。

建议顺序：

1. 先让纯转写稳定
2. 再测 `Qwen3-ForcedAligner-0.6B`
3. 最后再考虑批量和接口化

### 7.3 不建议先做容器化

在这台机器上，优先级应该是：

1. 驱动正常
2. 主模型跑通
3. 资源占用稳定
4. 再考虑 Docker

原因很简单：

- 现在最大的风险不在应用层
- 在 GPU 运行时和硬件兼容性

---

## 8. 我会怎么落地

如果这是我的机器，我会按下面顺序做：

1. 先重启，验证 `nvidia-smi`
2. 如果仍异常，重装 535 驱动并再次重启
3. 新建独立 `venv`
4. 先部署 `Qwen3-ASR-0.6B`
5. 用 `float16 + batch_size=1` 跑 10 到 20 个真实样本
6. 看显存、速度和识别质量
7. 如果中文术语识别不满意，再补测 `Fun-ASR-Nano`
8. 如果必须要时间戳，再接 `Qwen3-ForcedAligner-0.6B`

### 这套顺序的理由

- 它最少依赖这张旧卡不擅长的能力
- 它把问题拆成“先跑通，再提质，再加功能”
- 它能尽快得到一个可工作的结果

---

## 9. 最终建议

### 你这台机器，最现实的答案

- **第一选择：Qwen3-ASR-0.6B**
- **部署方式：transformers backend，不走 vLLM**
- **精度类型：先试 `float16`**
- **第二选择：Fun-ASR-Nano**
- **先不要碰：Qwen3-ASR-1.7B / streaming / vLLM**

### 什么时候再升级方案

- 你换到 Turing/Ampere/Ada/Hopper GPU
- 或者你有另一台更现代的推理机

到那时再考虑：

- `Qwen3-ASR-1.7B`
- `vLLM`
- `flash-attn`
- streaming 服务化

---

## 10. 参考来源

### 本机检查

- `uname -a`
- `free -h`
- `lscpu`
- `lspci`
- `lspci -k`
- `lsmod`
- `nvidia-smi`
- `/proc/driver/nvidia/version`
- `dpkg -l`

### 官方资料

- Qwen3-ASR GitHub: https://github.com/QwenLM/Qwen3-ASR
- Qwen3-ASR pyproject: https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/main/pyproject.toml
- vLLM GPU requirements: https://docs.vllm.ai/en/latest/getting_started/installation/gpu/
- FlashAttention README: https://github.com/Dao-AILab/flash-attention
- NVIDIA Legacy CUDA GPU Compute Capability: https://developer.nvidia.com/cuda-legacy-gpus
- NVIDIA Quadro desktop specs page: https://www.nvidia.com/en-zz/design-visualization/quadro-desktop-gpus/
- Fun-ASR GitHub: https://github.com/FunAudioLLM/Fun-ASR
