import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles, Heart } from 'lucide-react';

export default function GalleryPage() {
  const [projects, setProjects] = useState([]);
  const navigate = useNavigate();

  useEffect(() => {
    fetch(`http://${window.location.hostname}:3000/api/projects?sort=popular&limit=20`)
      .then(res => res.json())
      .then(data => setProjects(data))
      .catch(console.error);
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 p-8 text-white font-sans">
      <div className="max-w-6xl mx-auto space-y-8">
        
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-4xl font-bold flex items-center gap-3">
              <Sparkles className="text-blue-400" /> Community Gallery
            </h1>
            <p className="text-gray-400 mt-2">See what others have built with Hivemind Studio.</p>
          </div>
          <button 
            onClick={() => navigate('/')}
            className="px-6 py-2 bg-white text-black rounded-xl font-semibold hover:bg-gray-200 transition"
          >
            Build your own
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {projects.map(proj => (
            <div 
              key={proj.id} 
              onClick={() => navigate(`/app/${proj.id}`)}
              className="group cursor-pointer bg-gray-900 border border-gray-800 hover:border-blue-500/50 rounded-2xl p-6 transition-all duration-300 hover:shadow-xl hover:shadow-blue-500/10 flex flex-col justify-between"
            >
              <div>
                <p className="text-lg font-medium text-gray-200 line-clamp-3">"{proj.prompt}"</p>
                <div className="mt-4 text-xs text-gray-500 font-mono">
                  {proj.id.split('-')[0]} • {new Date(proj.created_at).toLocaleDateString()}
                </div>
              </div>
              <div className="mt-6 flex items-center gap-2 text-gray-400 group-hover:text-pink-400 transition-colors">
                <Heart className="w-4 h-4" /> <span className="text-sm font-medium">{proj.like_count}</span>
              </div>
            </div>
          ))}
          {projects.length === 0 && (
            <div className="col-span-full text-center py-20 text-gray-500">
              No builds yet. Be the first!
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
