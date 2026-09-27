import React, { useState, useEffect, useRef } from 'react';
import { Header } from './components/Header';
import { VoiceOrb } from './components/VoiceOrb';
import { ConversationTranscript } from './components/ConversationTranscript';
import { VoiceInputBar } from './components/VoiceInputBar';
import { VoiceSettingsModal } from './components/VoiceSettingsModal';
import { AgentState, VoiceSettings, VoiceName, LiveConnectionState, VoiceEvent } from './types';
import { 
  playPcmAudio, 
  stopCurrentAudio, 
  speakWithBrowser, 
  SpeechRecognitionController 
} from './utils/audioEngine';
import { LiveKitClient } from './utils/liveKitClient';

const INITIAL_GREETING = "Hi! I'm your desktop voice assistant. I can control your PC, launch apps, manage open windows, run terminal commands, and search the web.";

function makeVoiceEvent(
  kind: VoiceEvent['kind'],
  text: string,
  extra?: Partial<Pick<VoiceEvent, 'status' | 'tool' | 'id' | 'timestamp'>>
): VoiceEvent {
  return {
    id: extra?.id ?? `ve_${kind}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    kind,
    text,
    timestamp: extra?.timestamp ?? Date.now(),
    status: extra?.status,
    tool: extra?.tool,
  };
}

export default function App() {
  // Realtime voice and action events feed (user / activity / assistant)
  const [voiceEvents, setVoiceEvents] = useState<VoiceEvent[]>([
    makeVoiceEvent('assistant', INITIAL_GREETING, { id: 'init-greeting' }),
  ]);

  // Agent State & Speech
  const [agentState, setAgentState] = useState<AgentState>('idle');
  const [liveStatus, setLiveStatus] = useState<LiveConnectionState>('disconnected');
  const [isMuted, setIsMuted] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState('');
  const [inputVolume, setInputVolume] = useState(0);
  const [outputVolume, setOutputVolume] = useState(0);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isTestingVoice, setIsTestingVoice] = useState(false);

  // Voice Settings
  const [settings, setSettings] = useState<VoiceSettings>({
    selectedVoice: 'Kore',
    speechRate: 1.0,
    pitch: 1.0,
    continuousMode: false,
    theme: 'dark'
  });

  const recognizerRef = useRef<SpeechRecognitionController | null>(null);
  const liveClientRef = useRef<LiveKitClient | null>(null);
  const isListeningRef = useRef(false);
  const lastSpokenUserUtterance = useRef<string>('');
  const openUserEventIdRef = useRef<string | null>(null);
  const openAssistantEventIdRef = useRef<string | null>(null);

  // Initialize Live Audio Client
  useEffect(() => {
    const client = new LiveKitClient({
      onStatusChange: (status) => {
        setLiveStatus(status);
        if (status === 'connected') {
          setAgentState('idle');
        } else if (status === 'connecting') {
          setAgentState('processing');
        } else {
          setAgentState('idle');
        }
      },
      onUserTranscript: (text, isFinal) => {
        lastSpokenUserUtterance.current = text;
        setLiveTranscript(`You: ${text}`);
        setAgentState('listening');

        setVoiceEvents((prev) => {
          const openId = openUserEventIdRef.current;
          if (openId) {
            const idx = prev.findIndex((e) => e.id === openId && e.kind === 'user');
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = { ...next[idx], text, timestamp: Date.now() };
              return next;
            }
          }
          const ev = makeVoiceEvent('user', text);
          openUserEventIdRef.current = ev.id;
          return [...prev, ev];
        });
        if (isFinal) {
          openUserEventIdRef.current = null;
        }
      },
      onModelTranscript: (text, isFinal) => {
        setLiveTranscript(`Natasha: ${text}`);
        setAgentState('speaking');

        setVoiceEvents((prev) => {
          const openId = openAssistantEventIdRef.current;
          if (openId) {
            const idx = prev.findIndex((e) => e.id === openId && e.kind === 'assistant');
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = { ...next[idx], text, timestamp: Date.now() };
              return next;
            }
          }
          const ev = makeVoiceEvent('assistant', text);
          openAssistantEventIdRef.current = ev.id;
          return [...prev, ev];
        });
        if (isFinal) {
          openAssistantEventIdRef.current = null;
        }
      },
      onModelActivity: (payload) => {
        const text = (payload.text || payload.tool || 'Desktop action').trim();
        if (!text) return;
        const status = payload.phase || payload.status;
        setVoiceEvents((prev) => [
          ...prev,
          makeVoiceEvent('activity', text, {
            status: status || undefined,
            tool: payload.tool,
          }),
        ]);
      },
      onModelTurnComplete: (fullText) => {
        setLiveTranscript('');
        setAgentState('idle');
        openUserEventIdRef.current = null;

        const now = Date.now();
        const trimmed = fullText.trim();

        if (lastSpokenUserUtterance.current.trim()) {
          const userText = lastSpokenUserUtterance.current.trim();
          lastSpokenUserUtterance.current = '';
          setVoiceEvents((prev) => {
            const lastUser = [...prev].reverse().find((e) => e.kind === 'user');
            if (lastUser && lastUser.text === userText) return prev;
            return [...prev, makeVoiceEvent('user', userText, { timestamp: now - 500 })];
          });
        }

        if (!trimmed) {
          openAssistantEventIdRef.current = null;
          return;
        }

        setVoiceEvents((prev) => {
          const openId = openAssistantEventIdRef.current;
          if (openId) {
            const idx = prev.findIndex((e) => e.id === openId && e.kind === 'assistant');
            if (idx >= 0) {
              const next = [...prev];
              next[idx] = { ...next[idx], text: trimmed, timestamp: now };
              openAssistantEventIdRef.current = null;
              return next;
            }
          }
          const lastAssistant = [...prev].reverse().find((e) => e.kind === 'assistant');
          if (lastAssistant && lastAssistant.text === trimmed) {
            openAssistantEventIdRef.current = null;
            return prev;
          }
          openAssistantEventIdRef.current = null;
          return [...prev, makeVoiceEvent('assistant', trimmed, { timestamp: now })];
        });
      },
      onVolumeChange: (inVol, outVol) => {
        setInputVolume(inVol);
        setOutputVolume(outVol);
      },
      onError: (err) => {
        console.warn('[Live] Client reported error:', err);
        setLiveTranscript(`Error: ${err}`);
      },
    });

    liveClientRef.current = client;

    return () => {
      stopCurrentAudio();
      client.stop();
      recognizerRef.current?.abort();
    };
  }, []);

  // Toggle Live Duplex Voice Session
  const handleToggleLive = async () => {
    if (liveStatus === 'connected' || liveStatus === 'connecting') {
      liveClientRef.current?.stop();
      setLiveStatus('disconnected');
      setAgentState('idle');
      setLiveTranscript('');
      setIsMuted(false);
      openUserEventIdRef.current = null;
    } else {
      stopCurrentAudio();
      recognizerRef.current?.stop();
      isListeningRef.current = false;

      try {
        await liveClientRef.current?.start(settings.selectedVoice, []);
      } catch (err: any) {
        console.error('Failed to start live session:', err);
        setLiveStatus('error');
        setLiveTranscript(err?.message || 'Failed to start live session');
      }
    }
  };

  const handleToggleMute = () => {
    if (liveClientRef.current && liveStatus === 'connected') {
      const muted = liveClientRef.current.toggleMute();
      setIsMuted(muted);
    }
  };

  const handleStopSpeaking = () => {
    stopCurrentAudio();
    if (liveStatus === 'connected') {
      liveClientRef.current?.interrupt();
    }
    if (recognizerRef.current) {
      recognizerRef.current.stop();
      isListeningRef.current = false;
    }
    setAgentState(liveStatus === 'connected' ? 'listening' : 'idle');
  };

  const handleToggleListen = () => {
    if (agentState === 'listening') {
      handleStopSpeaking();
    }
  };

  const handleNewSession = () => {
    stopCurrentAudio();
    openUserEventIdRef.current = null;
    openAssistantEventIdRef.current = null;
    lastSpokenUserUtterance.current = '';
    setVoiceEvents([
      makeVoiceEvent('assistant', INITIAL_GREETING, { id: 'init-greeting-' + Date.now() }),
    ]);
  };

  const handleTestVoice = async (voice: VoiceName, rate: number) => {
    setIsTestingVoice(true);
    const testText = "Hi! I'm Natasha. This is how my voice sounds at this setting.";
    const onDone = () => setIsTestingVoice(false);

    if (voice === 'browser') {
      speakWithBrowser(testText, voice, rate, 1.0, undefined, onDone);
    } else {
      try {
        const res = await fetch('/api/tts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: testText, voiceName: voice })
        });
        const data = await res.json();
        if (data.audioBase64) {
          await playPcmAudio(data.audioBase64, onDone, rate);
        } else {
          speakWithBrowser(testText, voice, rate, 1.0, undefined, onDone);
        }
      } catch {
        speakWithBrowser(testText, voice, rate, 1.0, undefined, onDone);
      }
    }
  };

  const handleUserCommand = async (text: string) => {
    if (!text.trim()) return;
    const cmd = text.trim();
    const userEv = makeVoiceEvent('user', cmd);
    setVoiceEvents((prev) => [...prev, userEv]);

    if (liveStatus === 'connected' && liveClientRef.current) {
      liveClientRef.current.sendText(cmd);
    } else {
      const actEv = makeVoiceEvent('activity', 'Executing desktop command...', { tool: 'control_desktop', status: 'start' });
      setVoiceEvents((prev) => [...prev, actEv]);
      try {
        const res = await fetch('/api/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: cmd }),
        });
        const data = await res.json();
        const tier = data.tier || 'Reflex';
        const msg = data.message || 'Action executed.';
        setVoiceEvents((prev) => [
          ...prev,
          makeVoiceEvent('activity', `Completed via ${tier}`, { tool: data.action, status: 'done' }),
          makeVoiceEvent('assistant', msg),
        ]);
      } catch (err: any) {
        setVoiceEvents((prev) => [
          ...prev,
          makeVoiceEvent('activity', `Failed: ${err.message}`, { status: 'error' }),
        ]);
      }
    }
  };

  return (
    <div className="min-h-screen bg-[#050608] text-[#E0E2E6] flex flex-col selection:bg-emerald-400 selection:text-slate-950 font-sans relative overflow-x-hidden">
      {/* Background Glow Orbs */}
      <div 
        className="fixed inset-0 pointer-events-none z-0" 
        style={{ background: 'radial-gradient(circle at 50% 35%, #151821 0%, #050608 100%)', opacity: 0.85 }} 
      />
      <div className="fixed top-[-100px] left-[-100px] w-[500px] h-[500px] bg-emerald-500/10 blur-[140px] rounded-full pointer-events-none z-0" />
      <div className="fixed bottom-[-100px] right-[-100px] w-[550px] h-[550px] bg-cyan-500/10 blur-[160px] rounded-full pointer-events-none z-0" />

      {/* Header */}
      <Header
        agentState={agentState}
        liveStatus={liveStatus}
        isLiveActive={liveStatus === 'connected'}
        onToggleLive={handleToggleLive}
        onToggleSettings={() => setIsSettingsOpen(true)}
        onNewSession={handleNewSession}
        continuousMode={settings.continuousMode}
        onToggleContinuous={() => setSettings(s => ({ ...s, continuousMode: !s.continuousMode }))}
      />

      {/* Main Container: Focused single-page Orb & Transcript */}
      <main className="flex-1 flex flex-col max-w-4xl w-full mx-auto px-4 sm:px-6 relative z-10 py-6 items-center">
        {/* Centered Glowing Voice Orb */}
        <section aria-label="Voice Control Orb" className="w-full mb-6 flex justify-center">
          <VoiceOrb
            agentState={agentState}
            liveStatus={liveStatus}
            isLiveActive={liveStatus === 'connected'}
            onToggleLive={handleToggleLive}
            onToggleListen={handleToggleListen}
            onStopSpeaking={handleStopSpeaking}
            isMuted={isMuted}
            onToggleMute={handleToggleMute}
            liveTranscript={liveTranscript}
            continuousMode={settings.continuousMode}
            onToggleContinuous={() => setSettings(s => ({ ...s, continuousMode: !s.continuousMode }))}
            activeDocNames={[]}
            inputVolume={inputVolume}
            outputVolume={outputVolume}
          />
        </section>

        {/* Real-Time Transcript & Activity Feed */}
        <section aria-label="Voice Transcript" className="flex-1 w-full max-w-3xl my-2">
          <div className="border-t border-white/10 pt-4">
            <ConversationTranscript events={voiceEvents} />
          </div>
        </section>
      </main>

      {/* Sticky Bottom Voice & Input Strip */}
      <VoiceInputBar
        agentState={agentState}
        isLiveActive={liveStatus === 'connected'}
        onToggleListen={handleToggleListen}
        onToggleLive={handleToggleLive}
        onStopSpeaking={handleStopSpeaking}
        isProcessing={agentState === 'processing' || liveStatus === 'connecting'}
        onSubmitText={handleUserCommand}
      />

      {/* Voice Settings Modal */}
      <VoiceSettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        settings={settings}
        onUpdateSettings={(newVals) => setSettings(s => ({ ...s, ...newVals }))}
        onTestVoice={handleTestVoice}
        isTestingVoice={isTestingVoice}
      />
    </div>
  );
}
