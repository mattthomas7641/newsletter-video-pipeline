# Byte Sized AI News: live deploy branch

This branch is what the daily Cloud Run job runs. The project, its docs and setup steps are on
[`main`](https://github.com/mattthomas7641/newsletter-video-pipeline).

How this branch differs from `main`:

- It keeps the original writer prompt, which costs about $0.14 a video instead of $0.22.
- Stories that Jev marks as need-to-know are written first, and a dropped one is retried once.
- Each run sets the thumbnail again on the last two days' Shorts, once YouTube has finished processing them.
- The container runs as a non-root user.
