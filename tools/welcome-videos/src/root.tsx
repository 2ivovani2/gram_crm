import React from "react";
import { Composition, Still } from "remotion";
import { TutorialVideo, TutorialPoster } from "./tutorial";
import { tutorials } from "./data";

export const Root: React.FC = () => (
  <>
    {tutorials.map((tutorial) => (
      <React.Fragment key={tutorial.id}>
        <Composition
          id={tutorial.id}
          component={TutorialVideo}
          width={720}
          height={1280}
          fps={30}
          durationInFrames={tutorial.duration * 30}
          defaultProps={{ tutorial }}
        />
        <Still
          id={`${tutorial.id}-poster`}
          component={TutorialPoster}
          width={720}
          height={1280}
          defaultProps={{ tutorial }}
        />
      </React.Fragment>
    ))}
  </>
);
