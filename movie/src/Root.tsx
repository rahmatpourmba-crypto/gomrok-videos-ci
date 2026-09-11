import React from 'react';
import {Composition} from 'remotion';
import {CustomsQuestion, cqTiming} from './CustomsQuestion';
import type {CustomQ} from './CustomsQuestion';

const FPS = 30;

const DEFAULT_Q: CustomQ = {
  num: 249,
  question: '',
  options: ['', '', '', ''],
  correct: 1,
  reason: '',
  audio: {q: 12.22, o1: 7.44, o2: 4.82, o3: 5.28, o4: 5.04, a: 8.66, r: 13.97},
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="CustomsQuestion"
        component={CustomsQuestion}
        durationInFrames={cqTiming(DEFAULT_Q).end}
        fps={FPS}
        width={1080}
        height={1920}
        defaultProps={{q: DEFAULT_Q}}
        calculateMetadata={({props}) => {
          return {durationInFrames: cqTiming(props.q).end};
        }}
      />
    </>
  );
};