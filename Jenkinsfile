// code-nyang-e pipeline: inspects one project from projects.yml per build.
//   checkout -> test -> coverage -> review -> sonar -> report -> publish
//
// Jenkins credentials (Secret text): sonar-token, claude-token, github-token.
// The target repo is only read: it is cloned into target/ and built there.
// Rules: failed tests are a result (decision B, 2026-11-26): the build goes on, is marked
// UNSTABLE and publishes tests.failed; a build that cannot compile or run tests stops.
// A failed review publishes claude = null; a failed quality gate is a result, not an error.

pipeline {
  agent any

  options {
    disableConcurrentBuilds()
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '50'))
    timeout(time: 90, unit: 'MINUTES')
  }

  // Decision 2026-11-26: inspect on every main push, polled every 5 minutes
  // (the mini PC cannot receive webhooks). Polling covers this repo and the target.
  triggers {
    pollSCM('H/5 * * * *')
  }

  parameters {
    string(name: 'PROJECT_ID', defaultValue: 'rehab', trim: true, description: 'id in projects.yml')
    booleanParam(name: 'RUN_REVIEW', defaultValue: true, description: 'Run the Claude review (uses Max usage)')
    booleanParam(name: 'PUBLISH', defaultValue: false, description: 'Push to dasu-archive/dashboard (T11, off until M3)')
  }

  environment {
    INIT = "${WORKSPACE}/gradle/code-nyang-e.init.gradle"
    TARGET = "${WORKSPACE}/target"
    OUT = "${WORKSPACE}/out"
    SONAR_HOST_URL = 'http://sonarqube:9000'
    // Local copy of what is (or will be) on the dashboard, for history.json across runs.
    CNY_STATE = '/var/jenkins_home/code-nyang-e/state'
    GRADLE_OPTS = '-Dorg.gradle.daemon=false'
  }

  stages {
    stage('checkout') {
      steps {
        script {
          def project = readYaml(file: 'projects.yml').projects.find { it.id == params.PROJECT_ID }
          if (!project) {
            error("PROJECT_ID '${params.PROJECT_ID}' is not in projects.yml")
          }
          env.TARGET_REPO = project.repo
          env.TARGET_NAME = project.name
          env.TARGET_BRANCH = project.branch ?: 'main'
          env.RUN_AT = sh(script: 'TZ=Asia/Seoul date +%Y-%m-%dT%H:%M:%S+09:00', returnStdout: true).trim()
        }
        dir('out') { deleteDir() }
        dir('target') {
          git url: "https://github.com/${env.TARGET_REPO}.git", branch: env.TARGET_BRANCH
        }
        script {
          env.TARGET_COMMIT = sh(script: 'git -C target rev-parse HEAD', returnStdout: true).trim()
        }
        echo "inspecting ${env.TARGET_REPO}@${env.TARGET_BRANCH} ${env.TARGET_COMMIT} (run at ${env.RUN_AT})"
      }
    }

    stage('test') {
      steps {
        // rehab's gradlew has no exec bit; run it through sh instead of changing the repo.
        // cnyIgnoreTestFailures: failing tests do not fail Gradle; junit below marks UNSTABLE.
        dir('target') {
          sh 'sh ./gradlew --no-daemon --init-script "$INIT" -PcnyIgnoreTestFailures=true test'
        }
      }
      post {
        always {
          junit allowEmptyResults: true, testResults: 'target/build/test-results/test/*.xml'
        }
      }
    }

    stage('coverage') {
      steps {
        dir('target') {
          sh 'sh ./gradlew --no-daemon --init-script "$INIT" jacocoTestReport'
        }
      }
    }

    stage('review') {
      when { expression { params.RUN_REVIEW } }
      steps {
        catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE') {
          withCredentials([string(credentialsId: 'claude-token', variable: 'CLAUDE_CODE_OAUTH_TOKEN')]) {
            sh 'bash review/run.sh "$PROJECT_ID" "$TARGET" "$OUT/claude"'
          }
        }
      }
    }

    stage('sonar') {
      steps {
        // A failed gate makes the scanner exit non-zero; keep going and publish it as a result.
        catchError(buildResult: 'UNSTABLE', stageResult: 'UNSTABLE') {
          withCredentials([string(credentialsId: 'sonar-token', variable: 'SONAR_TOKEN')]) {
            dir('target') {
              sh '''
                sh ./gradlew --no-daemon --init-script "$INIT" sonar \
                  -Dsonar.projectKey="$PROJECT_ID" \
                  -Dsonar.projectName="$TARGET_NAME" \
                  -Dsonar.coverage.jacoco.xmlReportPaths=build/reports/jacoco/test/jacocoTestReport.xml \
                  -Dsonar.qualitygate.wait=true \
                  -Dsonar.qualitygate.timeout=600
              '''
            }
          }
        }
      }
    }

    stage('report') {
      when { expression { fileExists('report/build_report.py') } }
      steps {
        withCredentials([
          string(credentialsId: 'sonar-token', variable: 'SONAR_TOKEN'),
          string(credentialsId: 'claude-token', variable: 'CLAUDE_CODE_OAUTH_TOKEN'),
        ]) {
          sh '''
            python3 report/build_report.py \
              --project-id "$PROJECT_ID" \
              --projects-yml projects.yml \
              --target-dir "$TARGET" \
              --findings "$OUT/claude/findings.json" \
              --run-at "$RUN_AT" \
              --branch "$TARGET_BRANCH" \
              --build "$BUILD_NUMBER" \
              --state-dir "$CNY_STATE" \
              --out "$OUT/site"
            python3 schema/validate.py "$OUT/site"
          '''
        }
      }
    }

    stage('publish') {
      when { expression { params.PUBLISH } }
      steps {
        error('publish (T11) is not implemented yet')
      }
    }
  }

  post {
    always {
      archiveArtifacts artifacts: 'out/claude/findings.json, out/site/**', allowEmptyArchive: true
    }
  }
}
