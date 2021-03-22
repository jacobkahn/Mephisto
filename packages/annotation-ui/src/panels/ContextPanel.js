import React, { useContext } from "react";
import { Context } from "../model/Store";
import { Slider } from "@blueprintjs/core";

export default function ContextPanel() {
  const { get, sendRequest } = useContext(Context);

  const videoLayerData = get(["layers", "Video", "data"]);
  const videoLoaded = !!videoLayerData;

  return videoLoaded ? (
    <div>
      Current video time (seconds): {videoLayerData.playedSeconds}
      <div style={{ padding: 20 }}>
        <Slider
          min={0}
          max={videoLayerData.duration}
          stepSize={0.1}
          labelStepSize={1}
          labelPrecision={0}
          labelRenderer={(val) => {
            const mmss = new Date(val * 1000).toISOString().substr(14, 5);
            return <span key={mmss}>{mmss.replace("00:", ":")}</span>;
          }}
          onRelease={(value) => {
            sendRequest("Video", { type: "seek", payload: value });
          }}
          value={videoLayerData.playedSeconds}
          vertical={false}
        />
      </div>
    </div>
  ) : null;
}
