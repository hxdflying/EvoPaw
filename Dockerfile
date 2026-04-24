FROM python:3.11-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js 22（Claude Agent SDK 依赖 Claude Code CLI）
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# 安装 Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# 工作目录
WORKDIR /app

# Python 依赖（先复制 requirements.txt 利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码（skills 在 evopaw/ 内）
COPY evopaw/ evopaw/

# 数据目录（运行时挂载）
RUN mkdir -p /app/data

# Sub-Agent 沙盒路径软链接：
#   /workspace   → /app/data/workspace  （SKILL.md 中的 /workspace/ 前缀）
#   /mnt/skills  → /app/evopaw/skills   （SKILL.md 中的 {skill_base} 脚本路径）
RUN ln -s /app/data/workspace /workspace && \
    ln -s /app/evopaw/skills /mnt/skills

# 创建非 root 用户（Claude Code CLI 拒绝以 root 运行 bypassPermissions）
RUN useradd -m -s /bin/bash evopaw && chown -R evopaw:evopaw /app
USER evopaw

# 入口
CMD ["python", "-m", "evopaw.main"]
