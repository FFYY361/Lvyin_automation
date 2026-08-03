import { describe, expect, it } from "vitest";
import { labelMissingField, type PlayedMatchSnapshot, type SeasonOutcomeSnapshot } from "../types";
import { cartesianPairs, formatPlayedMatch, formatSeasonOutcome, matchTaskStatus, moveItem, parseNames } from "../utils";

describe("frontend workflow helpers", () => {
  it("normalizes personnel names without duplicates", () => {
    expect(parseNames("张三、李四, 张三\n王五")).toEqual(["张三", "李四", "王五"]);
  });

  it("builds date and competition combinations in input order", () => {
    expect(cartesianPairs(["08-08", "08-09"], ["male", "female"])).toEqual([
      { left: "08-08", right: "male" },
      { left: "08-08", right: "female" },
      { left: "08-09", right: "male" },
      { left: "08-09", right: "female" },
    ]);
  });

  it("reorders draft articles without mutating the source", () => {
    const source = [101, 102, 103];
    expect(moveItem(source, 2, 0)).toEqual([103, 101, 102]);
    expect(source).toEqual([101, 102, 103]);
  });

  it("displays backend result text and season outcomes", () => {
    const played = {
      game_id: 1,
      home: { team_id: 1, name: "环境学院", short_name: "环境" },
      away: { team_id: 2, name: "探微书院", short_name: "探微" },
      home_score: 2,
      away_score: 2,
      home_penalty: 5,
      away_penalty: 4,
      result_text: "2(5):2(4)",
      season: "2024~2025",
      competition_label: "甲",
      stage: "半决赛",
    } satisfies PlayedMatchSnapshot;
    expect(formatPlayedMatch(played)).toBe("环境 2(5):2(4) 探微");
    expect(formatPlayedMatch(played, true)).toBe("2024~2025 · 甲 · 半决赛｜环境 2(5):2(4) 探微");
    expect(formatPlayedMatch({ ...played, home_penalty: undefined, away_penalty: undefined, result_text: "3:0" })).toBe("环境 3:0 探微");
    expect(formatSeasonOutcome({ season: "2023~2024", competition_label: null, outcome: "未参赛" } satisfies SeasonOutcomeSnapshot)).toBe("2023~2024｜未参赛");
  });

  it("labels match completeness fields with teams and a safe fallback", () => {
    const matches = [{
      game_id: 4245,
      home: { team_id: 1, name: "车辆与运载学院", short_name: "车辆" },
      away: { team_id: 2, name: "未央书院", short_name: "未央" },
    }];
    expect(labelMissingField("matches.4245.writers", matches)).toBe("车辆 vs 未央 · 作者");
    expect(labelMissingField("matches.4245.body", matches)).toBe("车辆 vs 未央 · 正文");
    expect(labelMissingField("matches.999.body")).toBe("比赛 #999 · 正文");
  });

  it("maps all match task states with inactive taking precedence", () => {
    expect(matchTaskStatus({ active: false, task_open: true, claimed_by_user_id: 8 }).label).toBe("已失效");
    expect(matchTaskStatus({ active: true, task_open: false, claimed_by_user_id: null }).label).toBe("未开放");
    expect(matchTaskStatus({ active: true, task_open: true, claimed_by_user_id: null }).label).toBe("开放 · 未领取");
    expect(matchTaskStatus({ active: true, task_open: true, claimed_by_user_id: 8 }).label).toBe("开放 · 已领取");
  });
});
