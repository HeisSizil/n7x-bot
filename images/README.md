# images/ - pictures for trivia questions

Put picture files here (`.png`, `.jpg`, `.jpeg`, `.gif` or `.webp`, under 8 MB) and use them in a
trivia question with the `image` field. Write **only the file name**, not a path:

```json
{"question": "Which animal is this?", "answers": ["Okapi", "Zebra", "Horse", "Giraffe"], "correct": 0,
 "image": "okapi.jpg"}
```

The bot attaches the file to the question when it's asked.

You can also use a direct link instead of a file:

```json
"image": "https://example.com/okapi.jpg"
```

Tips:
- Only use pictures you're allowed to use (your own, or free licences like Wikimedia Commons / Unsplash).
- Keep file names simple: lowercase, no spaces (`eiffel_tower.jpg`).
- After adding questions, run `python check_trivia.py`. It tells you if an image file is missing.
