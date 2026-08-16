/**
 * src/components/SplashScreen.jsx
 *
 * Branded intro splash — shows the Horizon United Bank logo + name,
 * then auto-dismisses into the login screen after a short delay.
 *
 * Usage in App.jsx (see integration notes below):
 *   {showSplash
 *     ? <SplashScreen onFinish={() => setShowSplash(false)} />
 *     : <LoginForm ... />  // or whatever renders next
 *   }
 */

import { useEffect, useState } from "react";
import "../styles/splash.css";

const SPLASH_DURATION_MS = 2200; // total time shown before auto-dismiss
const FADE_OUT_MS = 400;         // must be <= SPLASH_DURATION_MS

export default function SplashScreen({ onFinish }) {
  const [fadingOut, setFadingOut] = useState(false);

  useEffect(() => {
    const fadeTimer = setTimeout(() => setFadingOut(true), SPLASH_DURATION_MS - FADE_OUT_MS);
    const finishTimer = setTimeout(() => onFinish(), SPLASH_DURATION_MS);
    return () => {
      clearTimeout(fadeTimer);
      clearTimeout(finishTimer);
    };
  }, [onFinish]);

  return (
    <div className={`splash-screen ${fadingOut ? "splash-fade-out" : ""}`}>
      <img src="/horizon-logo.png" alt="Horizon United Bank" className="splash-logo" />
    </div>
  );
}