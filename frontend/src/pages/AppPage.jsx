import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, ExternalLink, ThumbsUp } from 'lucide-react';
import { getApiUrl, getSessionId } from '../config';
import { withCsp } from '../sandbox';


export default function AppPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [html, setHtml] = useState('');
  const [likes, setLikes] = useState(0);

  useEffect(() => {
    fetch(`${getApiUrl()}/api/projects/${id}`)
      .then(res => res.json())
      .then(data => {
        if (data.artifact) setHtml(data.artifact.html);
        if (data.project) setLikes(data.project.like_count);
      })
      .catch(console.error);
  }, [id]);

  const handleLike = async () => {
    try {
      const sessionId = getSessionId();

      const res = await fetch(`${getApiUrl()}/api/projects/${id}/like`, {
        method: 'POST',
        headers: { 'x-session-id': sessionId }
      });
      if (res.ok) {
        setLikes(prev => prev + 1);
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-white">
      {/* Topbar */}
      <div className="h-12 border-b bg-gray-50 flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-4">
          <button onClick={() => navigate(-1)} className="p-1 hover:bg-gray-200 rounded text-gray-600">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <span className="text-sm font-medium text-gray-600">App Preview</span>
        </div>
        <div className="flex items-center gap-3">
          <button onClick={handleLike} className="flex items-center gap-2 px-3 py-1 bg-gray-200 hover:bg-gray-300 rounded text-sm text-gray-700 transition">
            <ThumbsUp className="w-4 h-4" /> {likes}
          </button>
          <a href={`data:text/html;charset=utf-8,${encodeURIComponent(html)}`} download="app.html" className="flex items-center gap-2 px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm transition">
            <ExternalLink className="w-4 h-4" /> Export HTML
          </a>
        </div>
      </div>

      {/* Frame */}
      <div className="flex-1 w-full relative">
        {!html ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500">Loading...</div>
        ) : (
          <iframe
            title="Standalone App"
            srcDoc={withCsp(html)}
            sandbox="allow-scripts"
            className="w-full h-full border-none"
          />
        )}
      </div>
    </div>
  );
}
