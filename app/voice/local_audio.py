"""Local CLI Audio Client for LiveKit Voice Agent.

Captures local microphone input via sounddevice and streams it to the LiveKit room,
while receiving and playing back the incoming agent speech through local speakers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np
import sounddevice as sd
from livekit import rtc

from app.config import settings
from app.voice.token_service import generate_livekit_token

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
NUM_CHANNELS = 1
BLOCK_SIZE = 960  # 20ms of audio at 48kHz


class LocalVoiceClient:
    """Streams local microphone and speaker audio to and from a LiveKit room."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.room: rtc.Room | None = None
        self._running = False
        self._input_stream: sd.InputStream | None = None
        self._output_stream: sd.OutputStream | None = None
        self._audio_source: rtc.AudioSource | None = None
        self._audio_queue: asyncio.Queue[rtc.AudioFrame] | None = None
        self._pump_task: asyncio.Task | None = None

    async def connect(self, room_name: str | None = None) -> str:
        """Connect to the LiveKit voice room and start audio I/O."""
        if self.room is None:
            self.room = rtc.Room()

        token_info = generate_livekit_token(
            room_name=room_name,
            participant_name="Desktop Local Mic",
        )
        url = token_info["url"]
        token = token_info["token"]
        assigned_room = token_info["room_name"]

        logger.info("Connecting local audio client to LiveKit: %s (room: %s)", url, assigned_room)

        # Setup incoming track handler
        @self.room.on("track_subscribed")
        def _on_track_subscribed(track: rtc.Track, publication: Any, participant: rtc.RemoteParticipant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info("Subscribed to agent audio track from %s", participant.identity)
                asyncio.create_task(self._play_incoming_audio(track))

        # Setup data packet listener for visual CLI feedback
        @self.room.on("data_received")
        def _on_data_received(data: rtc.DataPacket):
            try:
                import json
                from rich.console import Console
                msg = json.loads(data.data.decode("utf-8"))
                logger.info("Voice Event: %s", msg)
                if isinstance(msg, dict):
                    ev_type = msg.get("type")
                    c = Console()
                    if ev_type == "user_transcript":
                        if msg.get("final"):
                            c.print(f"\n[bold magenta]🎙️  [Heard Spoken Command][/bold magenta] [bold white]\"{msg.get('text')}\"[/bold white]")
                    elif ev_type == "agent_transcript":
                        c.print(f"[bold cyan]🤖 [Agent Response][/bold cyan] [white]{msg.get('text')}[/white]")
                    elif ev_type == "model_activity":
                        phase = msg.get("phase")
                        cmd = msg.get("command") or msg.get("text") or ""
                        if phase == "start" and cmd:
                            c.print(f"[bold yellow]⚙️  [Action Started][/bold yellow] [dim]{cmd}[/dim]")
                        elif phase == "done":
                            tier = msg.get("tier", "Reflex")
                            c.print(f"[bold green]✔  [Executed via {tier}][/bold green]\n")
            except Exception:
                pass

        await self.room.connect(url, token)
        logger.info("Local audio connected to room: %s", assigned_room)

        # Start microphone publisher
        self._running = True
        await self._start_microphone()

        return assigned_room

    async def _start_microphone(self):
        """Capture microphone and publish audio track to the room with SOURCE_MICROPHONE."""
        self._audio_source = rtc.AudioSource(self.sample_rate, NUM_CHANNELS)
        mic_track = rtc.LocalAudioTrack.create_audio_track("microphone", self._audio_source)
        publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)

        await self.room.local_participant.publish_track(mic_track, publish_options)
        logger.info("Published local microphone track to room (source=MICROPHONE).")

        self._audio_queue = asyncio.Queue(maxsize=100)
        self._pump_task = asyncio.create_task(self._pump_mic_audio())

        loop = asyncio.get_running_loop()

        def mic_callback(indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags):
            if not self._running or self._audio_source is None:
                return

            pcm_data = (indata * 32767).astype(np.int16).tobytes()
            frame = rtc.AudioFrame(
                data=pcm_data,
                sample_rate=self.sample_rate,
                num_channels=NUM_CHANNELS,
                samples_per_channel=frames,
            )
            if self._audio_queue and not self._audio_queue.full():
                loop.call_soon_threadsafe(self._audio_queue.put_nowait, frame)

        self._input_stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=NUM_CHANNELS,
            dtype="float32",
            blocksize=BLOCK_SIZE,
            callback=mic_callback,
        )
        self._input_stream.start()
        logger.info("Microphone input stream started.")

    async def _pump_mic_audio(self):
        """Pump captured microphone frames sequentially to the LiveKit audio source."""
        while self._running:
            try:
                frame = await self._audio_queue.get()
                if self._audio_source and not getattr(self._audio_source._ffi_handle, "disposed", False):
                    await self._audio_source.capture_frame(frame)
                self._audio_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Audio pump error: %s", e)

    async def _play_incoming_audio(self, track: rtc.Track):
        """Stream incoming agent voice chunks to local speakers."""
        audio_stream = rtc.AudioStream(track)
        output_stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=NUM_CHANNELS,
            dtype="int16",
        )
        output_stream.start()

        try:
            async for frame_event in audio_stream:
                if not self._running:
                    break
                frame = frame_event.frame
                data = np.frombuffer(frame.data, dtype=np.int16)
                await asyncio.to_thread(output_stream.write, data)
        except Exception as exc:
            logger.debug("Incoming audio playback ended: %s", exc)
        finally:
            output_stream.stop()
            output_stream.close()

    async def disconnect(self):
        """Disconnect and stop all audio devices."""
        self._running = False
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
            try:
                await self._pump_task
            except Exception:
                pass
            self._pump_task = None

        if self._input_stream:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None

        if self.room is not None and self.room.isconnected():
            await self.room.disconnect()
            logger.info("Local audio disconnected.")


# Global local voice client singleton
local_voice_client = LocalVoiceClient()
