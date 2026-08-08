import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles, ArrowRight, Activity } from 'lucide-react';

const EXAMPLES = [
  "A habit tracker with streaks",
  "A pomodoro timer with tasks",
  "A markdown notes app with local storage",
  "A simple currency converter"
];

export default function LandingPage() {
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [events, setEvents] = useState([]);
  const navigate = useNavigate();

  useEffect(() => {
    // Setup Firehose WebSocket
    const wsUrl = `ws://${window.location.hostname}:3000/ws/firehose`;
    const ws = new WebSocket(wsUrl);
    
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setEvents(prev => [data, ...prev].slice(0, 10)); // Keep last 10
      } catch (e) {
        console.error("Failed to parse WS message", e);
      }
    };
    
    return () => ws.close();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!prompt.trim() || loading) return;
    
    setLoading(true);
    try {
      const res = await fetch(`http://${window.location.hostname}:3000/api/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt })
      });
      const data = await res.json();
      if (data.id) {
        navigate(`/build/${data.id}`);
      }
    } catch (err) {
      console.error(err);
      alert("Failed to submit prompt");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4 bg-gray-950 text-white font-sans">
      <div className="max-w-2xl w-full space-y-8 text-center">
        
        <div className="space-y-4">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/10 text-blue-400 text-sm font-medium">
            <Sparkles className="w-4 h-4" />
            Hivemind Studio
          </div>
          <h1 className="text-5xl md:text-6xl font-bold tracking-tight bg-gradient-to-br from-white to-gray-500 bg-clip-text text-transparent">
            Build micro-apps at the speed of thought.
          </h1>
          <p className="text-gray-400 text-lg max-w-xl mx-auto">
            Type one sentence. Watch a live team of AI agents plan, build, and ship a working React app in seconds.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="relative group">
          <div className="absolute -inset-1 bg-gradient-to-r from-blue-500 to-purple-600 rounded-2xl blur opacity-25 group-hover:opacity-50 transition duration-1000 group-hover:duration-200"></div>
          <div className="relative flex items-center bg-gray-900 rounded-2xl ring-1 ring-white/10 p-2">
            <input
              type="text"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="What do you want to build?"
              className="flex-1 bg-transparent px-4 py-3 outline-none text-lg text-white placeholder-gray-500"
              disabled={loading}
            />
            <button
              type="submit"
              disabled={loading || !prompt.trim()}
              className="bg-white text-black px-6 py-3 rounded-xl font-semibold flex items-center gap-2 hover:bg-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? 'Starting...' : 'Build'}
              <ArrowRight className="w-5 h-5" />
            </button>
          </div>
        </form>

        <div className="flex flex-wrap items-center justify-center gap-2">
          {EXAMPLES.map((ex, i) => (
            <button
              key={i}
              onClick={() => setPrompt(ex)}
              className="px-4 py-2 rounded-full bg-gray-800/50 hover:bg-gray-800 border border-gray-700/50 text-sm text-gray-300 transition-colors"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      {/* Firehose Ticker */}
      <div className="fixed bottom-0 left-0 right-0 p-4 border-t border-gray-800 bg-gray-950/80 backdrop-blur">
        <div className="max-w-4xl mx-auto flex items-center gap-4 text-sm text-gray-400">
          <Activity className="w-5 h-5 text-green-400 animate-pulse" />
          <div className="flex-1 overflow-hidden">
            <div className="flex gap-4 animate-marquee whitespace-nowrap">
              {events.length === 0 ? "Waiting for live events..." : events.map((ev, i) => (
                <span key={i} className="inline-block bg-gray-900 px-3 py-1 rounded-md border border-gray-800">
                  {ev.type === 'agent.status' && `🤖 ${ev.agent} is ${ev.status}`}
                  {ev.type === 'build.completed' && `✨ Build ${ev.projectId.slice(0,6)} completed!`}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
