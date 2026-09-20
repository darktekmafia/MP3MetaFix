# Audio fixture

`tone.m4a` is a synthetic 440 Hz, 0.15-second mono AAC tone generated for format and metadata regression tests. It contains no user audio. Regenerate with:

```sh
ffmpeg -hide_banner -loglevel error -f lavfi -i sine=frequency=440:duration=0.15 -c:a aac -y tests/fixtures/tone.m4a
```

FFmpeg is not needed to run the automated tests. WAV and MP3 fixtures are generated in Python.
