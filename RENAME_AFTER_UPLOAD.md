# RENAME_AFTER_UPLOAD

## This bundle needs no renaming

Every file in this context bundle is a normal Markdown file with no leading dot:

- `CLAUDE.md`
- `ARCHITECTURE.md`
- `DECISIONS.md`
- `STATE.md`
- `README.md`
- `GLOSSARY.md`

GitHub's web uploader accepts all of them as-is. **Upload them to the repo root.**
This file (`RENAME_AFTER_UPLOAD.md`) is kept only so your usual per-upload
checklist stays consistent — you can delete it after uploading.

## Reminder about the dotfiles that already exist in the repo

Your `.gitignore` and `.env.example` were uploaded earlier as `gitignore.txt` and
`env.example.txt` (renamed to survive the web uploader). This context bundle does
**not** re-ship them, to avoid overwriting what's already there. If a fresh clone
misbehaves, confirm those two were renamed back to their leading-dot names after
their original upload.

## Where these files go

All six land in the **repo root**, alongside the existing `README`-adjacent files
and the two planning docs (`bastion-product-roadmap.md`,
`maritime-isr-execution-spec.md`). `CLAUDE.md` at the root is what Claude Code reads
on every invocation.
