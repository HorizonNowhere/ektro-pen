"""
生成 N 条真实品质的模拟 commit 数据。

用途:
- benchmark 基础数据
- 演示用户面板效果
- 验证查询性能

特点:
- 真实中文常用字 + 真实拼音
- 时间分布跨越数天（不是同一秒爆 1000 条）
- 模拟多应用切换
- 加入 5% 的"user_picked"标记
"""
from __future__ import annotations

import argparse
import io
import random
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore  # noqa: E402

# 真实拼音→中文样例（个性化场景）
COMMITS = [
    ("nihao", "你好"),
    ("woaini", "我爱你"),
    ("jintian", "今天"),
    ("mingtian", "明天"),
    ("tianqi", "天气"),
    ("zenmeyang", "怎么样"),
    ("xiexie", "谢谢"),
    ("buyong", "不用"),
    ("haode", "好的"),
    ("zaijian", "再见"),
    ("woyao", "我要"),
    ("kafei", "咖啡"),
    ("nailai", "拿来"),
    ("naitie", "拿铁"),
    ("v60", "V60"),
    ("chemex", "Chemex"),
    ("v60lvbei", "V60 滤杯"),
    ("shuiwen", "水温"),
    ("zaoshang", "早上"),
    ("xiawu", "下午"),
    ("wanshang", "晚上"),
    ("qichuang", "起床"),
    ("shuijiao", "睡觉"),
    ("kanshu", "看书"),
    ("xiezuo", "写作"),
    ("daima", "代码"),
    ("daimadaima", "代码 代码"),
    ("rust", "Rust"),
    ("python", "Python"),
    ("yibu", "异步"),
    ("yunxingshi", "运行时"),
    ("xianchengchi", "线程池"),
    ("biji", "笔记"),
    ("xiangmu", "项目"),
    ("changjing", "场景"),
    ("xuqiu", "需求"),
    ("sheji", "设计"),
    ("shixian", "实现"),
    ("celue", "策略"),
    ("buozhuo", "捕捉"),
    ("benciweili", "本次为例"),
    ("xiandai", "现代"),
    ("yongqi", "勇气"),
    ("zhuanzhu", "专注"),
    ("xinyi", "心意"),
    ("yongxin", "用心"),
    ("yixiawu", "一下午"),
    ("yikoukouqi", "一口气"),
    ("buduan", "不断"),
    ("xubu", "续部"),
    ("buduandiqian", "不断地前"),
    ("shenghua", "升华"),
    ("yidian", "一点"),
    ("yidiandiandi", "一点点地"),
    ("rongru", "融入"),
    ("rongxin", "用心"),
    ("manyi", "满意"),
    ("zhenxin", "真心"),
    ("youqu", "有趣"),
    ("hen", "很"),
    ("youyong", "有用"),
    ("hangbie", "行别"),
    ("changxi", "尝试"),
    ("zhihui", "智慧"),
    ("juedingxing", "决定性"),
    ("benzhi", "本质"),
    ("baoliu", "保留"),
    ("yiyi", "意义"),
    ("yu", "于"),
    ("zhongyu", "终于"),
    ("zhongdaod", "终到达"),
    ("shensi", "深思"),
    ("zixin", "自信"),
    ("zhongjie", "总结"),
    ("xiezuo", "写作"),
    ("yulai", "雨来"),
    ("xueye", "雪夜"),
    ("xinjie", "心结"),
    ("buyao", "不要"),
    ("xiwang", "希望"),
    ("nuli", "努力"),
    ("xinxiang", "心想"),
    ("shicheng", "事成"),
]

# 模拟应用
APPS = [
    "Code.exe",         # 30%
    "chrome.exe",       # 25%
    "WeChat.exe",       # 20%
    "Notepad.exe",      # 10%
    "WINWORD.EXE",      # 8%
    "Explorer.exe",     # 4%
    "Terminal.exe",     # 3%
]
APP_WEIGHTS = [30, 25, 20, 10, 8, 4, 3]


def generate(n: int, db_path: Path, days_span: int = 7, seed: int = 42):
    rnd = random.Random(seed)
    store = EktroMemoryStore(db_path)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days_span * 86_400_000

    inserted = 0
    for _ in range(n):
        pinyin, output = rnd.choice(COMMITS)
        app = rnd.choices(APPS, weights=APP_WEIGHTS)[0]
        ts = rnd.randint(start_ms, now_ms)
        user_picked = rnd.random() < 0.05
        duration = rnd.randint(200, 3000)

        outcome = store.log_commit(
            input_raw=pinyin,
            output=output,
            app_name=app,
            user_picked=user_picked,
            duration_ms=duration,
            timestamp=ts,
        )
        if outcome.result.is_committed:
            inserted += 1

    stats = store.stats()
    store.close()
    print(f"Inserted: {inserted} / {n}")
    print(f"Total commits: {stats['total_commits']}")
    print(f"Unique chars:  {stats['unique_chars']}")
    print(f"Unique pairs:  {stats['unique_phrase_pairs']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=500, help="number of commits")
    ap.add_argument("--db", type=Path, default=Path("E:/CLAUDE/EKTRO输入法/tests/memory/mock.db"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if args.db.exists():
        args.db.unlink()
        for ext in ("-wal", "-shm"):
            p = Path(str(args.db) + ext)
            if p.exists():
                p.unlink()

    print(f"Generating {args.n} mock commits over {args.days} days → {args.db}")
    generate(args.n, args.db, days_span=args.days, seed=args.seed)


if __name__ == "__main__":
    main()
