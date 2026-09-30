// Mevzuat Takip — kurum CI (Jenkins + Nexus + SonarQube). İki imaj üretir:
//   com.albaraka.ai/mevzuat-takip-core    → LAN Kubernetes (deploy/helm/mevzuat-takip)
//   com.albaraka.ai/mevzuat-takip-crawler → DMZ podman (deploy/dmz)
// Etiket: main dalı → latest (UAT), diğer dallar → dev; ayrıca her derleme <sürüm>-<BUILD_NUMBER> ile etiketlenir.
pipeline {
    agent any
    environment {
        REGISTRY   = "http://nexus.albaraka.com.tr:9099"
        REPO       = "nexus.albaraka.com.tr:9099/com.albaraka.ai"
        NEXUSCRED  = 'jenkinsBuildForNexus'
        // Nexus taban imajı (kurum onaylı: 3.12-slim-bullseye)
        BASE_IMAGE = "nexus.alb.albarakatech.com:9099/com.albaraka.python:3.12-slim-bullseye"
        PIP_INDEX  = "http://10.54.62.31:8080/repository/albaraka-python/simple"
        PIP_HOST   = "10.54.62.31"
        CA_URLS    = "https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-root-ca-2042.crt https://nexus.alb.albarakatech.com/repository/atg-raw-file/alb_ca/albaraka-sub-ca-2041.crt"
    }

    stages {
        stage('Docker Build') {
            steps {
                script {
                    env.CHANNEL_TAG = (env.BRANCH_NAME == 'main' || env.GIT_BRANCH?.endsWith('/main')) ? 'latest' : 'dev'
                    env.BUILD_TAG_V = "0.2.0-${env.BUILD_NUMBER}"
                    def args = "--build-arg BASE_IMAGE=${BASE_IMAGE} --build-arg PIP_INDEX_URL=${PIP_INDEX} " +
                               "--build-arg PIP_TRUSTED_HOST=${PIP_HOST} --build-arg CA_CERT_URLS='${CA_URLS}' " +
                               "-f backend/docker/Dockerfile"
                    docker.withRegistry(REGISTRY, NEXUSCRED) {
                        docker.build("${REPO}/mevzuat-takip-core:${BUILD_TAG_V}", "--target core ${args} .")
                        docker.build("${REPO}/mevzuat-takip-crawler:${BUILD_TAG_V}", "--target crawler ${args} .")
                    }
                }
            }
        }

        stage('Docker Deploy') {
            steps {
                script {
                    docker.withRegistry(REGISTRY, NEXUSCRED) {
                        ['mevzuat-takip-core', 'mevzuat-takip-crawler'].each { name ->
                            def img = docker.image("${REPO}/${name}:${BUILD_TAG_V}")
                            img.push()
                            img.push(env.CHANNEL_TAG)
                        }
                    }
                }
            }
        }

        stage('Quality Analysis') {
            steps {
                // Proje anahtarı, kaynak/test yolları: sonar-project.properties
                withSonarQubeEnv(installationName: 'sonarqube', credentialsId: 'sonarqube8-token') {
                    sh """
                    docker run \
                        --rm \
                        -v /home/jenkins/.atg-ca-certs.crt:/tmp/cacerts/atg-certs.crt \
                        -e SONAR_HOST_URL=${env.SONAR_HOST_URL} \
                        -e SONAR_TOKEN=${env.SONAR_AUTH_TOKEN} \
                        -v .:/usr/src \
                        sonarsource/sonar-scanner-cli
                    """
                }
            }
        }
    }
}
