"""Optional local CPU speech synthesis using Silero's MIT-licensed CIS base model.

Install the *optional* torch/torchaudio/omegaconf packages separately.
This worker runs outside the bot process to avoid keeping torch in bot RAM.
First use downloads the model via PyTorch Hub; subsequent uses are cached.
"""
import argparse
import sys
import wave
from pathlib import Path

VOICE_OPTIONS = ("ru_gamat", "ru_eduard")
SAMPLE_RATE = 24000
MODEL_ID = "v5_cis_base"


def render(input_file, output_file, speaker):
    if speaker not in VOICE_OPTIONS:
        raise ValueError("Неизвестный голос Silero")
    import torch
    from voice_broadcast import split_voice_text

    torch.set_num_threads(2)
    model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-models",
        model="silero_tts",
        language="ru",
        speaker=MODEL_ID,
        trust_repo=True,
    )
    model.to(torch.device("cpu"))
    text = Path(input_file).read_text(encoding="utf-8")
    chunks = split_voice_text(text, limit=110)
    if not chunks:
        raise ValueError("Пустой текст для озвучки")

    with wave.open(str(output_file), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        pause = b"\x00\x00" * int(0.20 * SAMPLE_RATE)
        for i, phrase in enumerate(chunks):
            samples = model.apply_tts(
                text=phrase, speaker=speaker, sample_rate=SAMPLE_RATE
            )
            # Save torch float waveform as 16-bit PCM.
            pcm = (samples.detach().cpu().clamp(-1, 1) * 32767).to(torch.int16)
            wav.writeframes(pcm.numpy().tobytes())
            if i != len(chunks) - 1:
                wav.writeframes(pause)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("textfile")
    parser.add_argument("wavfile")
    parser.add_argument("--speaker", required=True, choices=VOICE_OPTIONS)
    args = parser.parse_args()
    try:
        render(args.textfile, args.wavfile, args.speaker)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
