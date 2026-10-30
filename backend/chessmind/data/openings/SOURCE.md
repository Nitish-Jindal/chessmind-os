# Opening taxonomy data

`a.tsv` … `e.tsv` are copied unchanged from
[lichess-org/chess-openings](https://github.com/lichess-org/chess-openings)
at commit `712a58be746c143f4ab935b68acc50f4bac813eb` (2026-10-29).
They are released under CC0 1.0 (see `COPYING.txt`).

Columns: `eco`, `name`, `pgn` (the line that defines the opening).
`chessmind.services.opening_taxonomy` replays each line with python-chess
and keys the entry by the canonical EPD of the final position, so
transpositions resolve to the same name. To refresh, copy the five files from a
newer commit and update the hash above.
