from __future__ import annotations

import argparse
from pathlib import Path

from .chartgen import analyze_audio
from .config import DIFFICULTIES
from .game import play_chart
from .launcher import main as launcher_main


def main() -> None:
    parser = argparse.ArgumentParser(description="Rhythm4G: MP3 auto-chart rhythm game")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("app", help="Open the desktop launcher UI")

    gen = sub.add_parser("generate", help="Generate chart JSON from audio")
    gen.add_argument("audio")
    gen.add_argument("--difficulty", choices=list(DIFFICULTIES), default="hard")
    gen.add_argument("--output", default=None)
    gen.add_argument("--bpm", type=float, default=None, help="Manual BPM override")

    play = sub.add_parser("play", help="Play an existing chart JSON")
    play.add_argument("chart")

    auto = sub.add_parser("auto", help="Generate a chart and play immediately")
    auto.add_argument("audio")
    auto.add_argument("--difficulty", choices=list(DIFFICULTIES), default="hard")
    auto.add_argument("--output", default=None)
    auto.add_argument("--bpm", type=float, default=None, help="Manual BPM override")

    args = parser.parse_args()
    if args.command == "generate":
        out = analyze_audio(args.audio, args.difficulty, args.output, manual_bpm=args.bpm)
        print(f"Chart written: {out}")
    elif args.command == "play":
        play_chart(args.chart)
    elif args.command == "auto":
        out = analyze_audio(args.audio, args.difficulty, args.output, manual_bpm=args.bpm)
        print(f"Chart written: {out}")
        play_chart(out)
    elif args.command == "app" or args.command is None:
        launcher_main()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
