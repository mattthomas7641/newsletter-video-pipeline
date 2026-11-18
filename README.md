# Byte Sized AI News: live deploy branch

This branch is what the daily Cloud Run job runs. The project, its docs and setup steps are on
[`main`](https://github.com/mattthomas7641/newsletter-video-pipeline).

It has the same writer as `main` (merged in on 18 November 2026). How this branch differs from `main`:

- Stories that Jev marks as need-to-know are written first, and a dropped one is retried once.
- Each run sets the thumbnails again about 15 minutes after its last upload, because YouTube ignores one set on a Short
  that was just uploaded. The next day's run retries any that were missed.
- The container runs as a non-root user.
