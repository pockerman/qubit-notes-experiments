Feature: Optimizing a prompt of Qubit Notes
  As the developer of Qubit Notes
  I want to improve the prompts its agents are given, from what they were really asked
  So that each agent gets better, with a record of how its prompt was improved and why

  The experiments are kept apart from the app, in this repository: it never imports the app, never changes it, and
  only reads what it needs of it, from the app's database (QUBIT_NOTES_EXPERIMENT_APP_DATABASE_URL), with a
  connection that can only read. A prompt optimized here is given to the app in a step of its own, later; nothing here
  writes to the app. Nor is it a copy of the app: it reads only the tables an experiment needs, as it needs them, and
  the app's database is whatever the app has, e.g. its local one (start_local.sh), with what its students have done.

  An experiment varies one prompt, e.g. "quiz", and nothing else: the samples it is run on, the model that runs it and
  the judge are the same for every version of it. It optimizes the prompt by Self-Supervised Prompt Optimization
  (SPO; Xiang et al., 2025), which needs no correct answers, only the prompt's requirements:

    1. The starting prompt is run on a few samples (3 by default), and is the best so far.
    2. In each round, the optimizer, a model, is given the best prompt so far, its outputs and the requirements, and
       writes a new version of the prompt.
    3. The new version is run on the same samples.
    4. The judge, a model, compares the new version's outputs with the best version's, sample by sample, several
       times over (4 by default), with the two in one order half the times and the other order the other half, so
       that it doesn't favour the first or the second. The version preferred in more than half of the verdicts is the
       best so far; on a tie, the best stays.
    5. After the last round (10 by default), the best version is the experiment's result.

  SPO judges on a few samples, so a version can win on them and do worse on others. The result is therefore checked
  once more, on held-out samples (10 by default): samples of the same prompt, none of them among those it was
  optimized on. The judge compares the result's outputs on them with the starting prompt's, in the same way, and the
  result is recommended for the app only if it is preferred in more than half of those verdicts.

  Each agent's experiments are kept in experiments/agents/<agent>/spo/, e.g. experiments/agents/quiz-creation-agent/
  spo/ for the agent the prompt "quiz" is given to: its starting prompt (prompts/v1.md, a copy of the app's, checked
  by its hash), its models (agent.toml), and its requirements (requirements.md), which say what a good output of it
  is, e.g. for "quiz":
  questions that test understanding, not wording, each answered by the section it cites. The optimizer and the judge
  are both given them. A new version must keep what the app relies on: the placeholders the app fills in, and the form
  of the output the app reads, e.g. each question citing its section by its reference, S3. A version that doesn't
  loses its round, without being judged.

  The samples are what the app's agents were really given, rebuilt from its database:

    | prompt          | a sample is                                                                        |
    | study-assistant | a question a student asked, and the text of the sections its answer drew on       |
    | quiz            | the text of the section, material or topic a quiz was asked on, and how many questions |
    | summaries       | the text of the section, material or topic a summary was asked of                 |
    | flashcards      | the text of the section, material or topic a deck was asked of, and how many cards |

  They are drawn at random, with a seed, so an experiment can be run again on the same samples; or from the replies
  students rated down, e.g. the quizzes the assistant wrote in a conversation, to improve what they didn't like. They are only ever drawn from the material of the students
  named (QUBIT_NOTES_EXPERIMENT_STUDENT, the experimenter's own), as the material is theirs.

  Every model is a real one: the optimizer, the prompt's and the judge's, each named with its provider, e.g.
  anthropic:claude-sonnet-5-5. By default the prompt runs on the app's model, and the optimizer and the judge on a
  stronger one. These scenarios run with the cheapest model (QUBIT_NOTES_EXPERIMENT_TEST_MODEL, e.g.
  anthropic:claude-haiku-5-5), few samples and few rounds, on the app's database as it is: what the models answer, and
  which samples there are, differ from one run to the next, so they check what the experiment does with them, never
  what they are. A scenario that needs more samples than the app's database has is skipped, and says what to do in
  the app for it to have them.

  An experiment is tracked in Langfuse, in a project of its own, apart from the app's traces: its samples are a
  dataset, each round is a run of it, every call of the optimizer, the prompt and the judge is traced, and each version
  of the prompt is kept in Langfuse's prompt management. As Langfuse keeps traces for 30 days, the experiment is also
  kept here, in the agent's runs/<experiment>/: each version of the prompt, its outputs, the judge's verdicts and
  reasons, the held-out check, and what each round cost.

  Background:
    Given the app's database, read only
    And the student whose samples are drawn
    And the prompt "quiz" has its requirements

  Rule: An experiment reads the app, and never changes it

    Scenario: The app's database is only read
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of the student
      Then the experiment read the app's database with a connection that can only read
      And nothing in the app's database changed

    Scenario: The starting prompt is the version the app uses
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of the student
      Then the experiment started from the version of "quiz" the app's database records the student's quizzes were written with, with its hash

    Scenario: A prompt no agent here is given
      When I optimize the prompt "no-such-prompt" for 1 round, with 2 samples and 2 held out, of the student
      Then I am told there's no agent here for "no-such-prompt"

    Scenario: A prompt without requirements can't be optimized
      Given the student has at least 4 samples of "quiz" in the app
      And the agent of "quiz" has no requirements
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of the student
      Then I am told to write experiments/agents/quiz-creation-agent/spo/requirements.md first

  Rule: The samples are what the app's agents were really given

    Scenario: A quiz's sample is the text it was asked on
      Given the student has at least 3 samples of "quiz" in the app
      When I draw 3 samples of the prompt "quiz", of the student
      Then each sample is the text of the student's material that a quiz was asked on, as the app has it, and its number of questions

    Scenario: The same seed draws the same samples
      Given the student has at least 4 samples of "quiz" in the app
      And I have drawn 3 samples of the prompt "quiz", of the student, with the seed 7
      When I draw 3 samples of the prompt "quiz", of the student, with the seed 7
      Then they are the same samples

    Scenario: Samples from the replies students rated down
      Given the student has rated at least 1 quiz of the assistant down in the app
      When I draw as many samples of the prompt "quiz", of the student, from the quizzes rated down, as there are, and 1 more
      Then every sample is of a quiz the student rated down
      And I am told how many there were, fewer than asked

    Scenario: Only the material of the students named is drawn from
      Given the student has at least 3 samples of "quiz" in the app
      When I draw 3 samples of the prompt "quiz", of the student
      Then every sample is of the student's own material, or of a course shared with them

  Rule: Each round, the optimizer writes a new version, and the judge keeps the better

    Scenario: A round's new version is written from the best so far
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of the student
      Then round 1 has a new version of "quiz", written by the optimizer from the starting version, its outputs and the requirements
      And the new version was run on the same 2 samples as the starting version

    Scenario: The judge compares each sample in both orders
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of the student, the judge comparing each 4 times
      Then the judge compared each sample's two outputs 4 times, 2 with the new version's first and 2 with the best's first
      And each verdict is kept with the judge's reason

    Scenario: The best so far is the version the judge preferred more often
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 2 held out, of the student
      Then after each round, the best so far is the new version if it was preferred in more than half of the round's verdicts, else the best before it

    Scenario: A version that drops a placeholder loses its round
      Given the student has at least 2 samples of "quiz" in the app
      And a version of "quiz" without the placeholder {choices}
      When I run a round of "quiz" with that version, against the starting version, with 2 samples of the student
      Then the round is lost, as the version doesn't fill in {choices}
      And its outputs weren't judged

    Scenario: A version whose outputs the app couldn't read loses its round
      Given the student has at least 2 samples of "quiz" in the app
      And a version of "quiz" that asks for each question's section by its title, not its reference
      When I run a round of "quiz" with that version, against the starting version, with 2 samples of the student
      Then the round is lost, as the app couldn't use its quizzes

    Scenario: The best version after the last round is the result
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 3 rounds, with 2 samples and 2 held out, of the student
      Then the experiment has 3 rounds
      And its result is the version that was best after the last round, with the round it was written in

  Rule: The result is recommended only if it does better on samples it wasn't optimized on

    Scenario: The held-out samples are none of those the prompt was optimized on
      Given the student has at least 5 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 3 held out, of the student
      Then the held-out check was made on 3 samples of "quiz", none of them among the 2 it was optimized on

    Scenario: The result is compared with the starting prompt on the held-out samples
      Given the student has at least 5 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 3 held out, of the student
      Then if the result is a new version, the judge compared its outputs with the starting version's on each held-out sample, in both orders
      And the result is recommended if it was preferred in more than half of those verdicts, and not recommended otherwise

    Scenario: A result that is the starting prompt isn't checked
      Given an experiment of "quiz" whose result is its starting version, as no round found a better one
      When its result is checked on held-out samples
      Then no held-out check is made, and I am told the experiment found nothing better than its starting version

    Scenario: Too few samples to hold any out
      Given the student has at least 1 sample of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with all the student's samples of "quiz" and 1 held out
      Then I am told how many samples of "quiz" the student has, fewer than asked for, before anything is run

  Rule: An experiment is tracked in Langfuse, and kept here

    Scenario: The samples, the rounds and the versions are in Langfuse
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 2 held out, of the student
      Then Langfuse's experiment project has the experiment's samples and held-out samples as datasets
      And a run of it for each round and for the held-out check, with the judge's verdicts as scores
      And each version of "quiz" in its prompt management, the result labelled "best"

    Scenario: The experiment is kept here too
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 2 held out, of the student
      Then the agent's runs/<experiment>/ has, for each round, the version, its outputs, the judge's verdicts and reasons, and what it cost
      And the held-out check, and whether the result is recommended
      And the experiment's samples, seed, models and result

    Scenario: The app's traces and the experiment's are kept apart
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of the student
      Then nothing was traced in the app's Langfuse project

  Rule: An experiment stays within its budget

    Scenario: An experiment stops at its budget
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 10 rounds, with 2 samples and 2 held out, of the student, within a budget of $0.01
      Then the experiment stops before it costs more than $0.01
      And it is kept as stopped at its budget, with the rounds it ran and the best version so far, and no held-out check

    Scenario: What an experiment cost
      Given the student has at least 4 samples of "quiz" in the app
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of the student
      Then the experiment says what the optimizer, the prompt and the judge cost, in tokens and in dollars
