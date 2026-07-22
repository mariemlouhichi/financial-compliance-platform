import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import './index.css';
import { initTheme } from './theme';

initTheme();   // applique le thème sauvegardé AVANT le premier rendu (pas de flash)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
