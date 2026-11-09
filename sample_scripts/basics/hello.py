# --- scripthub ---
# name: Hello (Python)
# description: "The smallest useful script: a few parameters passed as --flags. Start here."
# category: Basics
# parameters:
#   - name: name
#     label: Name
#     type: text
#     default: World
#     required: true
#     max_length: 60
#   - name: greeting
#     label: Greeting
#     type: select
#     choices: [Hello, Hi, Good morning, Namaste]
#     default: Hello
#   - name: times
#     label: Times
#     type: number
#     default: 1
#     min: 1
#     max: 10
#   - name: shout
#     label: Shout
#     type: boolean
#     default: false
#     help: Upper case
# --- end scripthub ---
"""Greet someone. ScriptHub calls it as: hello.py --name World --greeting Hello --times 1 [--shout]"""
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--name", default="World")
parser.add_argument("--greeting", default="Hello")
parser.add_argument("--times", type=int, default=1)
parser.add_argument("--shout", action="store_true")
args = parser.parse_args()

line = f"{args.greeting}, {args.name}!"
if args.shout:
    line = line.upper()
for _ in range(args.times):
    print(line)
