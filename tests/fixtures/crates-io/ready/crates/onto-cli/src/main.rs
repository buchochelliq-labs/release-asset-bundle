// A copy of skills/onto.md, so the package carries it; the test keeps it in step.
const SKILL: &str = include_str!("../skills/onto.md");

fn main() {
    println!("{} {}", onto_core::NAME, SKILL.len());
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_skill_matches_the_original() {
        assert_eq!(super::SKILL, include_str!("../../../skills/onto.md"));
    }
}
