[Back to the README](../README.md)

# Running it automatically every day

In the cloud: [docs/cloud.md](cloud.md). On a Mac, a `launchd` job can run `run_daily.sh` every morning. See
`com.tldrbrainrot.daily.plist` in `~/Library/LaunchAgents/`.

```bash
launchctl list | grep tldrbrainrot                                     # is it loaded?
launchctl start com.tldrbrainrot.daily                                 # run now
tail -f pipeline.log                                                   # watch a run
launchctl unload ~/Library/LaunchAgents/com.tldrbrainrot.daily.plist   # stop the schedule
./venv/bin/python -m newsletter_video.status                           # today's per-video state
```

`run_daily.sh` wraps the run in `caffeinate -is` so the Mac doesn't idle-sleep during the
~100 minutes it takes (a closed lid still sleeps it; the run just resumes on wake).

## Approval mode

By default the scheduled run makes and posts everything on its own. Set `DAILY_MODE=plan`
(Settings → *What the scheduled run does* → *Plan, then ask me*) and the scheduled job stops
after the planning step: it fetches the newsletters, picks the stories (skipping any already
covered), writes the scripts (one writer call, about 3 cents) into `output/<date>/plan.json`,
and sends the usual notification ("Daily plan finished: 3 video(s) planned ... waiting for
your review"). Nothing is voiced, rendered or uploaded. The Design lab's plan review then shows *Plan
ready — review and render* until you have previewed, changed looks and pressed *Render all…*
or *Render selected…* (or run `python -m newsletter_video.render_plan <date> --final`), which
renders and uploads exactly as the full run would, under the same lock, watchdog and
`status.json` bookkeeping, and skips any story covered since planning. A plan the day already
has (made from the panel before the run) is kept, not replaced. *Run the full daily job now…* in
the panel always runs the full job, whatever `DAILY_MODE` says. `DAILY_MODE=full` (or unset)
is exactly the previous behaviour.
