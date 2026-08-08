export const getApiUrl = () => {
  return import.meta.env.VITE_API_URL || `http://${window.location.hostname}:3000`;
};

export const getWsUrl = () => {
  return import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:3000`;
};

// Anonymous identity, used for creator attribution and one-like-per-person.
export const getSessionId = () => {
  let id = localStorage.getItem('session_id');
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem('session_id', id);
  }
  return id;
};
