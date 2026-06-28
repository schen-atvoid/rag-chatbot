# `uv` vs `venv` — 对比分析

## 概述

- **`venv`**：Python 内置的虚拟环境工具，创建隔离的 Python 环境，但需要配合 `pip` 安装依赖。
- **`uv`**：Astral 开发的现代 Python 包管理器（Rust 编写），一站式替代 `pip`、`venv`、`pip-tools`、`pyenv`。

---

## 功能对比

### 速度

- **venv + pip**：慢，pip 按顺序逐个解析依赖
- **uv**：快 10-100 倍，Rust 实现，并行解析

### 安装步骤

- **venv + pip**：3 步 — `venv` → `activate` → `pip install`
- **uv**：1 步 — `uv sync`

### 锁文件

- **venv + pip**：手动维护，`pip freeze > requirements.txt`
- **uv**：自动生成 `uv.lock`，类似 Cargo/Poetry

### Python 版本管理

- **venv + pip**：需要额外工具，如 `pyenv`
- **uv**：内置，`uv python install 3.13`

### 依赖解析

- **venv + pip**：基础回溯算法
- **uv**：现代 SAT 求解器

### 生态成熟度

- **venv + pip**：通用，Python 自带，历史悠久
- **uv**：较新但快速普及，Ansible/Homebrew 等大项目已采用

### 本项目配置

- **venv + pip**：`requirements.txt`（手动维护版本号）
- **uv**：`pyproject.toml` + `uv.lock`（自动锁定）

---

## 隔离机制对比

### 环境位置

- **venv + pip**：`.venv/`，需手动创建
- **uv**：`.venv/`，`uv sync` 自动创建

### 删除方式

- **venv + pip**：`rm -rf .venv/`
- **uv**：`rm -rf .venv/`（完全一样，干净利落）

### 全局缓存

- **venv + pip**：缓存在 `~/.cache/pip/`，删项目不删缓存
- **uv**：缓存在 `~/.cache/uv/`，跨项目共享，删项目不影响其他项目

### Python 版本

- **venv + pip**：直接使用系统安装的 Python
- **uv**：自动下载管理 Python 版本，存放在 `~/.local/share/uv/python/`，完全独立于系统 Python

### 激活方式

- **venv + pip**：每次都要 `source .venv/bin/activate`
- **uv**：无需激活，`uv run python` 自动使用项目 `.venv` 里的环境

### 误操作风险

- **venv + pip**：`pip install` 忘了先激活环境会污染全局 Python
- **uv**：所有操作限定在项目内，不可能意外装到系统

---

## 本项目实际使用示例

### 用 `venv` 安装

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install chromadb==1.0.15 anthropic==0.58.2 sentence-transformers==5.0.0 \
            fastapi==0.116.1 uvicorn==0.35.0 python-multipart==0.0.20 python-dotenv==1.1.1
```

### 用 `uv` 安装

```bash
uv sync
```

---

## 结论：推荐 `uv`

1. 本项目官方首选 `uv sync`（见 CLAUDE.md）
2. `uv.lock` 保证跨机器完全一致的依赖版本
3. 解析和安装速度远超 pip
4. 一个命令搞定所有事情
5. 项目要求 Python ≥3.13，`uv` 可自动获取管理对应版本

唯一保留 `venv` 的场景：无法安装 `uv` 的受限环境，或不使用 `pyproject.toml` 的旧项目。本项目不在此列。
