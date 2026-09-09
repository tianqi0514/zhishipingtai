import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { installWebCryptoDigestFallback } from './hash';
import './styles.css';
import './enhancements.css';

installWebCryptoDigestFallback();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
