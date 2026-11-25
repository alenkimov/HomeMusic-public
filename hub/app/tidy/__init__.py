"""«Наведение порядка»: the Hub looks through the library by itself and offers what to put right.

What it looks for, by the sections of its page:

* **Названия** — what a name should not carry («(Official Video)», «[Lyrics]», the artist written in front of the
  song's name: `rules.clean_title`), and Russian names written in Latin letters («Mikhail Krug» → «Михаил Круг»,
  for artists, albums and tracks alike: the AI, `ai.cyrillic`).
* **Версии и дубли** — a track that is another recording of a song the library has («Trampoline (Sped Up)» of
  «Trampoline»: joined to it as its version, `app.library.versions`), and a recording that is there twice (told by
  the sound, `app.library.acoustic.doubles`: the better file stays, the other goes to the trash).
* **Исполнители** — two artists that are one («LSP» and «ЛСП»: by how the names read, then the AI for what looks
  alike only).
* **Альбомы** — one album in pieces: the page of «Объединение альбомов» (`app.api.library`), shown here.
* **Тексты** — the lines of a text that are not the song: links, «подписывайтесь», a site's own header.

Nothing is changed by itself. What is found is a proposal (`TidyProposal`): what the subject is now (`was`), what
it would be (`to`), who says so — a rule, or the AI and how sure it is. Somebody accepts it, and it is done the
way a listener would do it by hand (the same functions, the same journal of edits); an accepted one can be taken
back while the subject is still what it made of it. A rejected one is not offered again; a subject that changes is
a new question. The AI is paid for by the month (`Settings.tidy_ai_usd_month`), and a question it answered «нет»
is not paid for twice (`TidyAsked`).

Modules: `rules` (what needs no AI), `ai` (what does), `kinds` (how each kind is done and undone), `scan` (the
look-through, a job of the queue — daily and on request), `proposals` (the list, the decisions, the journal).
"""
