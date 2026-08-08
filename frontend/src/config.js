export const getApiUrl = () => {
  return import.meta.env.VITE_API_URL || `http://${window.location.hostname}:3000`;
};

export const getWsUrl = () => {
  return import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:3000`;
};
