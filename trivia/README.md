# trivia/ - the trivia questions

One file per category. The bot re-reads a file automatically when you save it (no restart).

| File | Category in /trivia |
|---|---|
| `movies.json` | 🎬 Movies |
| `anime.json` | 🍥 Anime |
| `animals.json` | 🐾 Animals |
| `geography.json` | 🌍 Geography |
| `fun_maths.json` | ➗ Fun Maths (the bot also makes new maths questions in code) |
| `fun_facts.json` | 💡 Fun Facts |
| `ai_web3.json` | 🤖 AI & Web3 |

🎲 Mixed uses all of them.

## Adding a question

Add one `{...}` block inside the `"questions": [ ... ]` list (mind the commas between blocks):

```json
{
 "question": "Which planet is known as the Red Planet?",
 "answers": ["Mars", "Venus", "Jupiter", "Mercury"],
 "correct": 0,
 "explanation": "Iron oxide (rust) on its surface makes Mars look red."
}
```

- `answers`: exactly 4, all different (max 80 characters each).
- `correct`: the position of the right answer: `0` = first, `1` = second, `2` = third, `3` = fourth.
  Answers are shuffled when shown, so the position doesn't matter to players.
- `explanation` (optional): one short sentence shown after answering.
- `image` (optional): a file name in the `images/` folder, or an `https://` link. See `images/README.md`.
- Emoji work too: `"question": "Which animal is this? 🦒"`.

Rules: easy, checkable facts only. No song lyrics or long quotes. Nothing about N7X itself
(that goes in `facts.md`).

## Check your work

```
python check_trivia.py
```

It shows every problem with the file and question number. A broken question is skipped by
the bot; a broken file is skipped completely (one line in the Termux log) until you fix it.
