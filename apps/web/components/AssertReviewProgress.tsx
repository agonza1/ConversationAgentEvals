'use client';

import { useEffect, useState } from 'react';
import styles from './AssertReviewProgress.module.css';

/** An indeterminate status, not a prediction of the judge's verdict or progress. */
export function AssertReviewProgress() {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className={styles.card} role="region"
      aria-label="ASSERT review in progress">
      <svg className={styles.illustration} viewBox="0 0 120 88" fill="none"
        aria-hidden="true" focusable="false">
        <circle cx="99" cy="14" r="3" fill="#b56cf5" />
        <circle cx="12" cy="70" r="2" fill="#24d4c1" />
        <path d="M109 37v6m-3-3h6" stroke="#a99af4" strokeWidth="2" strokeLinecap="round" />
        <g className={styles.reply}>
          <path d="M76 9h24a9 9 0 0 1 9 9v11a9 9 0 0 1-9 9H86l-8 6v-6h-2a9 9 0 0 1-9-9V18a9 9 0 0 1 9-9Z"
            fill="#a66be8" />
          <path d="M79 21h18m-18 7h11" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
        </g>
        <g className={styles.buddy}>
          <path d="M23 23h31a15 15 0 0 1 15 15v14a15 15 0 0 1-15 15H35l-12 9V65A15 15 0 0 1 8 52V38a15 15 0 0 1 15-15Z"
            fill="#24d4c1" />
          <g className={styles.eyes}>
            <ellipse cx="28" cy="42" rx="3" ry="4" fill="#242830" />
            <ellipse cx="49" cy="42" rx="3" ry="4" fill="#242830" />
          </g>
          <path d="M30 53q8 7 16 0" stroke="#242830" strokeWidth="2.5" strokeLinecap="round" />
          <path d="M18 33q3-4 8-4" stroke="#b8fff1" strokeWidth="2.5" strokeLinecap="round" />
        </g>
        <g className={styles.lens}>
          <path d="m88 69 16 13" stroke="#a99af4" strokeWidth="7" strokeLinecap="round" />
          <circle cx="78" cy="59" r="17" fill="#faf9ff" stroke="#a99af4" strokeWidth="4" />
          <path d="M70 55h16m-16 7h10" stroke="#4e5563" strokeWidth="2.5" strokeLinecap="round" />
          <path d="M67 50q3-4 7-4" stroke="#ded7ff" strokeWidth="2" strokeLinecap="round" />
        </g>
      </svg>
      <div className={styles.copy} role="status" aria-live="polite" aria-atomic="true">
        <strong>ASSERT is reviewing<span className={styles.dots} aria-hidden="true"><i /><i /><i /></span></strong>
        <p>Checking the conversation evidence. Your automatic result stays unchanged.</p>
      </div>
      <div className={styles.activity}>
        <span>{elapsed >= 30 ? 'Still reviewing the evidence' : 'Waiting for ASSERT’s response'}</span>
        <span role="timer" aria-live="off" aria-label="Review elapsed time">{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')} elapsed</span>
      </div>
      <div className={styles.track} aria-hidden="true"><span /></div>
    </div>
  );
}
