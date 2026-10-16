"""
check_trivia.py - Checks every trivia file before you use it.

Run from the project folder (no bot needed):
    python check_trivia.py

It lists every problem with the file name and question number, plus questions that
appear in two different files. Exit code 0 = all good, 1 = something to fix.
"""

import os
import sys

import trivia_bank


def main():
    problems = 0
    total = 0
    seen = {}   # question text -> file it was first seen in
    for key, (label, filename) in trivia_bank.CATEGORIES.items():
        if not filename:
            continue
        path = os.path.join(trivia_bank.TRIVIA_DIR, filename)
        if not os.path.isfile(path):
            print(f"⚠️  trivia/{filename}: missing ({label} will have no file questions)")
            continue
        try:
            questions, file_problems = trivia_bank.read_file(path)
        except (OSError, ValueError) as error:
            print(f"❌ trivia/{filename}: the whole file is broken: {error}")
            print("   Tip: paste it into https://jsonlint.com to find the exact spot.")
            problems += 1
            continue
        for problem in file_problems:
            print(f"❌ trivia/{filename}: {problem}")
        problems += len(file_problems)
        for question in questions:
            text = question["question"].lower()
            if text in seen and seen[text] != filename:
                print(f"❌ trivia/{filename}: \"{question['question']}\" is also in trivia/{seen[text]}")
                problems += 1
            seen.setdefault(text, filename)
        images = sum(1 for q in questions if q["image"])
        extra = f", {images} with a picture" if images else ""
        print(f"{'✅' if not file_problems else '⚠️ '} trivia/{filename}: {len(questions)} good question(s){extra}")
        total += len(questions)

    print(f"\nTotal: {total} questions from files (Fun Maths also makes new ones in code).")
    if problems:
        print(f"Found {problems} problem(s). Broken questions are skipped by the bot until fixed.")
        return 1
    print("Everything looks good! 🎉")
    return 0


if __name__ == "__main__":
    sys.exit(main())
