import React, { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Play, Check, Loader2, ArrowRight } from 'lucide-react';

const CSP = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com; style-src 'unsafe-inline'; img-src https: data:;">`;

export default function BuildPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  
  const [status, setStatus] = useState('queued');
  const [agents, setAgents] = useState({
    architect: { status: 'idle' },
    builder: { status: 'idle' },
    reviewer: { status: 'idle' }
  });
  const [transcript, setTranscript] = useState('');
  const [html, setHtml] = useState('');
  
  const transcriptRef = useRef(null);

  useEffect(() => {
    // Initial fetch
    fetch(`http://${window.location.hostname}:3000/api/projects/${id}`)
      .then(res => res.json())
      .then(data => {
        if (data.project) setStatus(data.project.status);
        if (data.artifact) setHtml(data.artifact.html);
      })
      .catch(console.error);

    // Setup WS
    const wsUrl = `ws://${window.location.hostname}:3000/ws/projects/${id}`;
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'agent.status') {
          setAgents(prev => ({
            ...prev,
            [msg.agent]: { status: msg.status }
          }));
        } else if (msg.type === 'agent.token') {
          setTranscript(prev => prev + msg.delta);
          // Scroll to bottom
          if (transcriptRef.current) {
            transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
          }
        } else if (msg.type === 'artifact.update') {
          setHtml(msg.html);
        } else if (msg.type === 'build.completed') {
          setStatus('done');
          setAgents(prev => ({
            architect: { status: 'done' },
            builder: { status: 'done' },
            reviewer: { status: 'done' }
          }));
        } else if (msg.type === 'build.failed') {
          setStatus('failed');
          alert("Build failed: " + msg.error);
        }
      } catch (e) {
        console.error("Failed to parse message", e);
      }
    };

    return () => ws.close();
  }, [id]);

  const StatusIcon = ({ status }) => {
    if (status === 'thinking' || status === 'revising') return <Loader2 className="w-5 h-5 animate-spin text-blue-400" />;
    if (status === 'done') return <Check className="w-5 h-5 text-green-400" />;
    return <Play className="w-5 h-5 text-gray-600" />;
  };

  return (
    <div className="min-h-screen bg-gray-950 flex flex-col md:flex-row text-white">
      
      {/* Sidebar - Agents & Transcript */}
      <div className="w-full md:w-1/3 flex flex-col border-r border-gray-800 bg-gray-900/50">
        <div className="p-4 border-b border-gray-800">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            Build Progress
            {status === 'running' && <span className="flex h-2 w-2 rounded-full bg-blue-500 animate-pulse ml-2"></span>}
          </h2>
        </div>
        
        {/* Agent Cards */}
        <div className="p-4 space-y-3 shrink-0">
          {['architect', 'builder', 'reviewer'].map((agent) => (
            <div key={agent} className={`p-4 rounded-xl border transition-all duration-300 ${
              agents[agent].status !== 'idle' ? 'bg-gray-800 border-gray-700' : 'bg-gray-900/50 border-gray-800 opacity-50'
            }`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <StatusIcon status={agents[agent].status} />
                  <span className="font-medium capitalize">{agent}</span>
                </div>
                <span className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
                  {agents[agent].status}
                </span>
              </div>
            </div>
          ))}
        </div>

        {/* Transcript */}
        <div className="flex-1 p-4 overflow-hidden flex flex-col min-h-0">
          <h3 className="text-sm font-semibold text-gray-500 mb-2 uppercase tracking-wider shrink-0">Live Output</h3>
          <div 
            ref={transcriptRef}
            className="flex-1 bg-gray-950 rounded-xl p-4 overflow-y-auto font-mono text-xs text-gray-300 border border-gray-800 whitespace-pre-wrap break-all"
          >
            {transcript || "Waiting for output..."}
          </div>
        </div>

        {/* Actions */}
        {status === 'done' && (
          <div className="p-4 border-t border-gray-800 bg-gray-900 flex gap-2">
            <button 
              onClick={() => navigate(`/app/${id}`)}
              className="flex-1 bg-blue-600 hover:bg-blue-500 text-white py-3 rounded-xl font-medium transition-colors flex items-center justify-center gap-2"
            >
              Open App <ArrowRight className="w-4 h-4" />
            </button>
            <button 
              onClick={() => {
                const remixPrompt = prompt("What do you want to change?");
                if (remixPrompt) {
                  fetch(`http://${window.location.hostname}:3000/api/projects/${id}/remix`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt: remixPrompt })
                  })
                  .then(res => res.json())
                  .then(data => {
                    if (data.id) navigate(`/build/${data.id}`);
                  })
                  .catch(console.error);
                }
              }}
              className="px-4 bg-gray-800 hover:bg-gray-700 text-white py-3 rounded-xl font-medium transition-colors flex items-center justify-center"
            >
              Remix
            </button>
          </div>
        )}
      </div>

      {/* Main Preview */}
      <div className="w-full md:w-2/3 flex flex-col bg-gray-800 p-4">
        <div className="flex-1 bg-white rounded-2xl overflow-hidden shadow-2xl ring-1 ring-black/10 flex items-center justify-center relative">
          {!html ? (
            <div className="text-gray-400 flex flex-col items-center">
              <Loader2 className="w-8 h-8 animate-spin mb-4" />
              Waiting for first build pass...
            </div>
          ) : (
            <iframe
              title="Preview"
              srcDoc={CSP + html}
              sandbox="allow-scripts"
              className="w-full h-full border-none"
            />
          )}
        </div>
      </div>

    </div>
  );
}
